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
    """Return the close-to-close return after N future trading sessions.

    Despite the historical parameter name, horizon_days is a count of observed
    trading sessions, not calendar days. The old SQL used INTERVAL '252 days',
    which measured roughly eight months and mislabeled it as a one-year horizon.
    """
    y = int(as_of[:4])
    urls = [f"{HF}/nse/year={z}/nse_{z}.parquet" for z in range(y, min(y + 2, 2027))]
    c = duckdb.connect()
    paths = "[" + ",".join(repr(x) for x in urls) + "]"
    syms = ",".join("'" + s.replace("'", "''") + "'" for s in symbols)
    q = f"""WITH p AS (
        SELECT symbol,date,close FROM read_parquet({paths},union_by_name=true)
        WHERE symbol IN ({syms})
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
        SELECT symbol,arg_min(close,date) FILTER(
            WHERE forward_session={int(horizon_days)}
        ) fx
        FROM future GROUP BY symbol
    )
    SELECT a.symbol,a.px,b.fx,b.fx/a.px-1 forward_return
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
    cutoff = x["fundamental_score"].quantile(.75)
    x["_selected"] = x["fundamental_score"] >= cutoff
    by_date = x.groupby("decision_date")
    rows = []
    for date, g in by_date:
        top = g.loc[g["_selected"], "forward_return"].dropna()
        allr = g["forward_return"].dropna()
        if len(top) and len(allr):
            rows.append({
                "decision_date": date,
                "top_mean": float(top.mean()),
                "all_mean": float(allr.mean()),
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
        facts = (
            facts_df[facts_df.symbol.isin(pool.symbol)]
            if not facts_df.empty
            else pd.DataFrame()
        )
        pit_symbols = set(facts.symbol.astype(str).unique()) if not facts.empty else set()
        missing = [s for s in pool.symbol.astype(str).tolist() if s not in pit_symbols]
        if missing:
            try:
                dyn = NSEPIT()
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

        fs = score_fundamentals(
            pool,
            facts.to_dict(orient="records") if isinstance(facts, pd.DataFrame) else facts,
        )
        if fs.empty:
            continue
        fr = forward_returns(fs.symbol.tolist(), as_of)
        z = fs.merge(fr, on="symbol", how="inner")
        z["fundamental_score"] = z[MODULES].mean(axis=1, skipna=True)
        z["fundamental_module_count"] = z[MODULES].notna().sum(axis=1)
        z["decision_date"] = as_of

        # Preserve every module explicitly for downstream statistical validation.
        rows.extend(z[REQUIRED_COLUMNS].to_dict("records"))

    out = pd.DataFrame(rows)
    if out.empty:
        return {"status": "NO_VALIDATION_OBSERVATIONS"}

    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    if missing:
        return {"status": "VALIDATION_SCHEMA_ERROR", "missing_columns": missing}

    q = out.fundamental_score.quantile(.75)
    top = out[out.fundamental_score >= q].forward_return.dropna()
    allr = out.forward_return.dropna()

    statistical = _statistical_validation(out)
    top_mean = float(top.mean()) if len(top) else None
    all_mean = float(allr.mean()) if len(allr) else None
    excess_return = (top_mean - all_mean) if top_mean is not None and all_mean is not None else None
    relative_return_ratio = (top_mean / all_mean) if top_mean is not None and all_mean is not None and all_mean > 1e-12 else None

    return {
        "status": "PIT_MODULE_VALIDATION_COMPLETE",
        "observations": int(len(out)),
        "decision_dates": int(out.decision_date.nunique()),
        "top_quartile_forward_return": top_mean,
        "all_forward_return": all_mean,
        "selection_lift": excess_return,
        "selection_lift_definition": "top-quartile mean forward return minus all-observation mean forward return",
        "relative_return_ratio": relative_return_ratio,
        "positive_hit_rate": float((top > 0).mean()) if len(top) else None,
        "next_step": "statistical gate: clustered bootstrap confidence intervals + two-sided permutation tests + Benjamini-Hochberg FDR",
        "pit_source": "NSE PIT store plus dynamic NSE PIT fallback",
        "statistical_validation": statistical,
    }
