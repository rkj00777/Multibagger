import pandas as pd, duckdb, numpy as np
from core.market_data import nse_cross_section
from core.nse_pit import NSEPIT
from core.pit_store import load_facts
from multibagger.modules import score_fundamentals

HF = "https://huggingface.co/datasets/tejhq/indian-markets/resolve/main"
MODULES = [
    "valuation_gap",
    "earnings_acceleration",
    "cash_conversion",
    "reinvestment_roic",
    "governance_balance_sheet",
]
REQUIRED_COLUMNS = [
    "decision_date",
    "symbol",
    "fundamental_score",
    "fundamental_module_count",
    "fundamental_evidence",
    "forward_return",
    *MODULES,
]


def forward_returns(symbols, as_of, horizon_days=252):
    """Return forward close-to-close returns after 6/12/24-month trading horizons.

    Horizons are observed trading sessions (126, 252, 504), not calendar days.
    Prices are restricted to ordinary NSE equity series and deduplicated by
    symbol/date so alternate series cannot inflate the session count.
    """
    y = int(as_of[:4])
    urls = [
        f"{HF}/nse/year={z}/nse_{z}.parquet"
        for z in range(y, min(y + 3, 2027))
    ]
    c = duckdb.connect()
    paths = "[" + ",".join(repr(x) for x in urls) + "]"
    syms = ",".join("'" + s.replace("'", "''") + "'" for s in symbols)
    q = f"""WITH raw AS (
        SELECT symbol,date,close,series
        FROM read_parquet({paths},union_by_name=true)
        WHERE symbol IN ({syms})
          AND series IN ('EQ','BE','BZ') AND close>0
    ),
    p AS (
        SELECT symbol,date,close
        FROM raw
        QUALIFY row_number() OVER(
            PARTITION BY symbol,date
            ORDER BY CASE series WHEN 'EQ' THEN 0 WHEN 'BE' THEN 1 ELSE 2 END
        )=1
    ),
    a AS (
        SELECT symbol,arg_max(close,date) FILTER(WHERE date<=DATE '{as_of}') px
        FROM p GROUP BY symbol
    ),
    future AS (
        SELECT symbol,date,close,
               row_number() OVER(PARTITION BY symbol ORDER BY date) AS forward_session
        FROM p
        WHERE date>DATE '{as_of}'
    ),
    b AS (
        SELECT symbol,
          arg_min(close,date) FILTER(WHERE forward_session=126) f126,
          arg_min(close,date) FILTER(WHERE forward_session=252) f252,
          arg_min(close,date) FILTER(WHERE forward_session=504) f504
        FROM future GROUP BY symbol
    )
    SELECT a.symbol,a.px,b.f126,b.f252,b.f504,
           b.f126/a.px-1 forward_return_6m,
           b.f252/a.px-1 forward_return_12m,
           b.f504/a.px-1 forward_return_24m,
           b.f252/a.px-1 forward_return
    FROM a LEFT JOIN b USING(symbol)"""
    out = c.execute(q).fetchdf()
    c.close()
    return out


def _cluster_frame(out):
    """Collapse stock-date observations to decision-date means for inference."""
    x = out.copy()
    x["decision_date"] = pd.to_datetime(x["decision_date"], errors="coerce")
    x = x.dropna(subset=["decision_date"])
    if x.empty:
        return pd.DataFrame(columns=["decision_date", *MODULES, "forward_return", "fundamental_score"])
    agg = {m: "mean" for m in MODULES}
    agg.update({"forward_return": "mean", "fundamental_score": "mean"})
    return x.groupby("decision_date", as_index=False).agg(agg)


def _bootstrap_mean_ci(values, n_boot=10000, seed=42):
    x = pd.Series(values).dropna().astype(float).to_numpy()
    if len(x) == 0:
        return {"n": 0, "mean": None, "ci_low": None, "ci_high": None}
    rng = np.random.default_rng(seed)
    means = rng.choice(x, (n_boot, len(x))).mean(axis=1)
    return {
        "n": int(len(x)),
        "mean": float(x.mean()),
        "ci_low": float(np.quantile(means, .025)),
        "ci_high": float(np.quantile(means, .975)),
    }


def _cluster_bootstrap_mean_ci(out, value_col, n_boot=10000, seed=42):
    dates = pd.to_datetime(out["decision_date"], errors="coerce")
    x = out.assign(_date=dates).dropna(subset=["_date", value_col])
    if x.empty:
        return {"independent_decision_dates": 0, "mean": None, "ci_low": None, "ci_high": None}
    date_means = x.groupby("_date")[value_col].mean().dropna().to_numpy(float)
    result = _bootstrap_mean_ci(date_means, n_boot=n_boot, seed=seed)
    result["independent_decision_dates"] = int(len(date_means))
    return result


def _spearman_permutation_p(x, y, n_perm=10000, seed=42):
    a = pd.to_numeric(pd.Series(x), errors="coerce")
    b = pd.to_numeric(pd.Series(y), errors="coerce")
    m = a.notna() & b.notna()
    a = a[m].rank(method="average").to_numpy(float)
    b = b[m].rank(method="average").to_numpy(float)
    n = len(a)
    if n < 4:
        return {"n": int(n), "rho": None, "p_value": None, "method": "two-sided_permutation"}
    ac = a - a.mean()
    bc = b - b.mean()
    denom = np.sqrt((ac * ac).sum() * (bc * bc).sum())
    rho = float((ac * bc).sum() / denom) if denom else 0.0
    rng = np.random.default_rng(seed)
    ge = 1
    for _ in range(n_perm):
        bp = rng.permutation(b)
        pc = float((ac * (bp - bp.mean())).sum() / denom) if denom else 0.0
        if abs(pc) >= abs(rho):
            ge += 1
    return {
        "n": int(n),
        "rho": rho,
        "p_value": float(ge / (n_perm + 1)),
        "method": "two-sided_permutation",
    }


def _bh_fdr(pvals):
    vals = [float(p) if p is not None and np.isfinite(p) else np.nan for p in pvals]
    finite = sorted([(i, p) for i, p in enumerate(vals) if np.isfinite(p)], key=lambda z: z[1])
    q = [None] * len(vals)
    m = len(finite)
    prev = 1.0
    for rank, (i, p) in reversed(list(enumerate(finite, 1))):
        prev = min(prev, p * m / rank)
        q[i] = float(prev)
    return q


def _selection_lift_cluster_ci(out, n_boot=10000, seed=103):
    x = out.copy()
    x["decision_date"] = pd.to_datetime(x["decision_date"], errors="coerce")
    x["fundamental_score"] = pd.to_numeric(x["fundamental_score"], errors="coerce")
    x["forward_return"] = pd.to_numeric(x["forward_return"], errors="coerce")
    x = x.dropna(subset=["decision_date", "fundamental_score", "forward_return"])
    if x.empty:
        return {"status": "NO_DATA"}
    by_date = x.groupby("decision_date")
    rows = []
    for date, g in by_date:
        # Select the top quartile independently within each decision date;
        # a global cutoff would silently change the selected fraction over time.
        cutoff = g["fundamental_score"].quantile(.75)
        top = g.loc[g["fundamental_score"] >= cutoff, "forward_return"].dropna()
        allr = g["forward_return"].dropna()
        if len(top) and len(allr):
            rows.append({
                "decision_date": date,
                "top_mean": float(top.mean()),
                "all_mean": float(allr.mean()),
                "top_positive_hit_rate": float((top > 0).mean()),
            })
    d = pd.DataFrame(rows)
    if d.empty:
        return {"status": "NO_VALID_DATE_CLUSTERS"}
    observed_diff = float((d.top_mean - d.all_mean).mean())
    # A ratio is interpretable as lift only when the baseline mean return is
    # positive. Dividing two negative returns can make a worse strategy look
    # like it has >1x "lift"; use excess return as the primary metric instead.
    valid_ratio = d.loc[d.all_mean > 1e-12].copy()
    ratio = float((valid_ratio.top_mean / valid_ratio.all_mean).mean()) if not valid_ratio.empty else None
    rng = np.random.default_rng(seed)
    diffs, ratios = [], []
    top_arr = d.top_mean.to_numpy(float)
    all_arr = d.all_mean.to_numpy(float)
    for _ in range(n_boot):
        idx = rng.integers(0, len(d), len(d))
        diffs.append(float((top_arr[idx] - all_arr[idx]).mean()))
        den = all_arr[idx]
        mask = den > 1e-12
        if mask.any():
            ratios.append(float(np.mean(top_arr[idx][mask] / den[mask])))
    return {
        "status": "OK",
        "independent_decision_dates": int(len(d)),
        "top_quartile_mean": float(d.top_mean.mean()),
        "all_mean": float(d.all_mean.mean()),
        "top_quartile_positive_hit_rate": float(d.top_positive_hit_rate.mean()),
        "difference_in_means": observed_diff,
        "difference_ci_low": float(np.quantile(diffs, .025)),
        "difference_ci_high": float(np.quantile(diffs, .975)),
        "ratio_mean_where_valid": ratio,
        "ratio_ci_low_where_valid": float(np.quantile(ratios, .025)) if ratios else None,
        "ratio_ci_high_where_valid": float(np.quantile(ratios, .975)) if ratios else None,
        "ratio_valid_denominator_dates": int(len(valid_ratio)),
        "ratio_denominator_excluded_dates": int(len(d) - len(valid_ratio)),
        "ratio_interpretation": "Reported only for dates with positive baseline mean return; excess return is the primary comparison.",
        "denominator_near_zero": bool((d.all_mean.abs() <= 1e-12).any()),
    }


def _statistical_validation(out):
    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"Statistical validation missing required columns: {missing}")

    clusters = _cluster_frame(out)
    independent_dates = int(clusters["decision_date"].nunique()) if not clusters.empty else 0
    tests = []
    for mod in MODULES:
        t = _spearman_permutation_p(
            clusters[mod],
            clusters["forward_return"],
            n_perm=10000,
            seed=42,
        ) if not clusters.empty else {
            "n": 0, "rho": None, "p_value": None, "method": "two-sided_permutation"
        }
        t["module"] = mod
        tests.append(t)

    for t, q in zip(tests, _bh_fdr([x["p_value"] for x in tests])):
        t["q_value"] = q
        t["fdr_significant"] = bool(q is not None and q <= .05)

    cutoff = out.fundamental_score.quantile(.75)
    top = out[out.fundamental_score >= cutoff].forward_return.dropna()
    allr = out.forward_return.dropna()
    lift = _selection_lift_cluster_ci(out, n_boot=10000, seed=103)

    insufficient = independent_dates < 8
    gate = "INSUFFICIENT_INDEPENDENT_SAMPLE" if insufficient else (
        "PASS" if any(t["fdr_significant"] for t in tests) else "NOT_PASSED"
    )

    return {
        "bootstrap_iterations": 10000,
        "bootstrap_method": "decision_date_cluster_bootstrap",
        "independent_decision_dates": independent_dates,
        "minimum_independent_decision_dates_for_statistical_gate": 8,
        "top_quartile_mean_ci": _cluster_bootstrap_mean_ci(out[out.fundamental_score >= cutoff], "forward_return", n_boot=10000, seed=101),
        "all_observations_mean_ci": _cluster_bootstrap_mean_ci(out, "forward_return", n_boot=10000, seed=102),
        "selection_lift_ci": lift,
        "module_tests": tests,
        "fdr_method": "Benjamini-Hochberg",
        "fdr_alpha": .05,
        "statistical_gate": gate,
        "statistical_warning": (
            "Inference is clustered by decision date. "
            "A significant module is evidence, not proof; production validation requires "
            "adequate independent dates, effect direction, confidence intervals, and FDR control."
        ),
    }


def validate(months, top_n=25):
    rows = []
    date_diagnostics = []
    # Historical backtests use only locally archived PIT facts by default.
    # Live NSE requests are useful for current ingestion, but doing hundreds of
    # ad-hoc network lookups inside a historical backtest is slow, rate-limited,
    # and makes coverage hard to reproduce. Enable only for a deliberate audit.
    use_dynamic_pit_fallback = str(
        __import__("os").getenv("MBE_ENABLE_DYNAMIC_PIT_FALLBACK", "0")
    ).strip().lower() in {"1", "true", "yes"}
    # One client per walk-forward run enables catalog/XBRL caching across dates.
    dyn = NSEPIT() if use_dynamic_pit_fallback else None
    for as_of in months:
        px = nse_cross_section(as_of)
        if px.empty:
            continue
        px["technical"] = (
            px.ret_63d.rank(pct=True) * 25
            + px.ret_126d.rank(pct=True) * 35
            + px.ret_252d.rank(pct=True) * 40
        )
        pool = px[
            (px.avg_turnover_60d >= 2_000_000) & px.ret_126d.notna()
        ].nlargest(top_n, "technical")
        facts_df = load_facts(as_of)
        diag = {
            "decision_date": as_of,
            "price_universe_symbols": int(px.symbol.nunique()),
            "liquid_price_pool": int(len(pool)),
            "local_pit_fact_rows": int(len(facts_df)),
            "local_pit_symbols": int(facts_df.symbol.nunique()) if not facts_df.empty else 0,
            "dynamic_pit_fallback_enabled": bool(use_dynamic_pit_fallback),
        }
        facts = (
            facts_df[facts_df.symbol.isin(pool.symbol)]
            if not facts_df.empty
            else pd.DataFrame()
        )
        pit_symbols = set(facts.symbol.astype(str).unique()) if not facts.empty else set()
        missing = [s for s in pool.symbol.astype(str).tolist() if s not in pit_symbols]
        diag["pool_symbols_with_local_pit_facts"] = int(len(pool) - len(missing))
        diag["pool_symbols_missing_local_pit_facts"] = int(len(missing))
        if missing and use_dynamic_pit_fallback and dyn is not None:
            try:
                rows_dyn = []
                for sym in missing:
                    try:
                        rows_dyn.extend(dyn.facts(sym, as_of) or [])
                    except Exception:
                        pass
                if rows_dyn:
                    ddf = pd.DataFrame(rows_dyn)
                    facts = pd.concat([facts, ddf], ignore_index=True) if not facts.empty else ddf
            except Exception:
                pass

        diag["pit_fact_rows_after_optional_fallback"] = int(len(facts))
        diag["pit_symbols_after_optional_fallback"] = int(facts.symbol.nunique()) if not facts.empty else 0
        fs = score_fundamentals(
            pool,
            facts.to_dict(orient="records") if isinstance(facts, pd.DataFrame) else facts,
        )
        diag["fundamental_scored_symbols"] = int(fs.symbol.nunique()) if not fs.empty else 0
        if fs.empty:
            diag.update({
                "forward_price_symbols": 0,
                "usable_observations": 0,
                "status": "NO_FUNDAMENTAL_SCORES",
            })
            date_diagnostics.append(diag)
            continue
        fr = forward_returns(fs.symbol.tolist(), as_of)
        z = fs.merge(fr, on="symbol", how="inner")
        diag["forward_price_symbols"] = int(fr.symbol.nunique()) if not fr.empty else 0
        diag["usable_observations"] = int(z.forward_return.notna().sum()) if "forward_return" in z else 0
        diag["status"] = "OK" if diag["usable_observations"] else "NO_FORWARD_RETURN"
        date_diagnostics.append(diag)
        z["fundamental_score"] = z[MODULES].mean(axis=1, skipna=True)
        z["fundamental_module_count"] = z[MODULES].notna().sum(axis=1)
        z["decision_date"] = as_of

        # Preserve every module and every forward horizon for downstream
        # statistical validation. Null future horizons remain visible as
        # immature observations rather than being mislabeled as failures.
        horizon_columns = ["forward_return_6m", "forward_return_12m", "forward_return_24m"]
        keep = REQUIRED_COLUMNS + [col for col in horizon_columns if col in z.columns]
        rows.extend(z[keep].to_dict("records"))

    out = pd.DataFrame(rows)
    if out.empty:
        return {
            "status": "INSUFFICIENT_PIT_COVERAGE",
            "observations": 0,
            "decision_dates": 0,
            "pit_source": "LOCAL_ARCHIVED_PIT_STORE_ONLY",
            "dynamic_pit_fallback_enabled": bool(use_dynamic_pit_fallback),
            "date_diagnostics": date_diagnostics,
            "next_step": "Backfill a timestamped free PIT fundamentals archive; do not substitute current fundamentals into historical dates.",
        }

    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    if missing:
        return {"status": "VALIDATION_SCHEMA_ERROR", "missing_columns": missing}

    horizon_validation = {}
    for label, column in [
        ("6m_126_sessions", "forward_return_6m"),
        ("12m_252_sessions", "forward_return_12m"),
        ("24m_504_sessions", "forward_return_24m"),
    ]:
        if column not in out.columns:
            continue
        frame = out.copy()
        frame["forward_return"] = pd.to_numeric(frame[column], errors="coerce")
        valid = frame.dropna(subset=["forward_return"])
        if valid.empty:
            horizon_validation[label] = {
                "status": "NO_MATURE_FORWARD_RETURNS",
                "observations": 0,
                "decision_dates": 0,
            }
            continue
        stats = _statistical_validation(frame)
        lift = stats.get("selection_lift_ci", {})
        horizon_validation[label] = {
            "status": "OK",
            "observations": int(len(valid)),
            "decision_dates": int(valid.decision_date.nunique()),
            "top_quartile_forward_return": lift.get("top_quartile_mean"),
            "all_forward_return": lift.get("all_mean"),
            "selection_lift": lift.get("difference_in_means"),
            "positive_hit_rate": lift.get("top_quartile_positive_hit_rate"),
            "relative_return_ratio": (
                lift.get("top_quartile_mean") / lift.get("all_mean")
                if lift.get("top_quartile_mean") is not None
                and lift.get("all_mean") is not None
                and lift.get("all_mean") > 1e-12 else None
            ),
            "statistical_validation": stats,
        }

    # Preserve the 12-month horizon as the backward-compatible top-level result.
    primary = horizon_validation.get("12m_252_sessions", {})
    statistical = primary.get("statistical_validation", {})
    top_mean = primary.get("top_quartile_forward_return")
    all_mean = primary.get("all_forward_return")
    excess_return = primary.get("selection_lift")
    relative_return_ratio = primary.get("relative_return_ratio")

    return {
        "status": "PIT_MODULE_VALIDATION_COMPLETE",
        "observations": int(len(out)),
        "decision_dates": int(out.decision_date.nunique()),
        "top_quartile_forward_return": top_mean,
        "all_forward_return": all_mean,
        "selection_lift": excess_return,
        "selection_lift_definition": "within-date top-quartile mean forward return minus within-date all-observation mean forward return",
        "relative_return_ratio": relative_return_ratio,
        "positive_hit_rate": primary.get("positive_hit_rate"),
        "next_step": "Require adequate independent decision dates, positive out-of-sample excess return, confidence intervals and FDR control before promotion.",
        "pit_source": "LOCAL_ARCHIVED_PIT_STORE_ONLY" if not use_dynamic_pit_fallback else "LOCAL_ARCHIVED_PIT_STORE_PLUS_DYNAMIC_NSE_FALLBACK",
        "dynamic_pit_fallback_enabled": bool(use_dynamic_pit_fallback),
        "date_diagnostics": date_diagnostics,
        "horizon_validation": horizon_validation,
        "statistical_validation": statistical,
    }
