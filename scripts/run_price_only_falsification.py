"""Price-only historical falsification of a blind momentum/early-trend screen.

Uses only public TejHQ NSE daily OHLCV/turnover history. No fundamentals, no
known-winner labels, and no future information in the ranking. At each fixed
quarterly decision date, select the top decile of a liquid universe using
63/126-session price momentum. Outcomes are measured only after selection.
Confidence intervals resample decision dates (not stock rows) to avoid
pretending repeated cross-sectional observations are independent.
"""
from __future__ import annotations
import json, os
from pathlib import Path
import numpy as np
import pandas as pd
import duckdb
from core.market_data import nse_cross_section, _url

DATES = pd.date_range("2012-03-30", "2023-03-31", freq="QE").strftime("%Y-%m-%d").tolist()
HORIZONS = {"6m_126_sessions":126, "12m_252_sessions":252, "24m_504_sessions":504}
BOOTSTRAPS = 5000
SEED = 617


def _future_prices(as_of: str) -> pd.DataFrame:
    year = int(as_of[:4])
    years = range(max(2010, year-2), min(2026, year+3)+1)
    paths = "[" + ",".join(repr(_url(y)) for y in years) + "]"
    con = duckdb.connect()
    q = f"""
      SELECT CAST(date AS DATE) date, upper(symbol) symbol, close
      FROM read_parquet({paths}, union_by_name=true)
      WHERE date >= DATE '{as_of}' AND close > 0
      ORDER BY symbol, date
    """
    try:
        out = con.execute(q).fetchdf()
    finally:
        con.close()
    out["date"] = pd.to_datetime(out["date"])
    out["symbol"] = out["symbol"].astype(str).str.upper()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    return out.dropna(subset=["date","symbol","close"]).drop_duplicates(["symbol","date"])


def _evaluate(as_of: str) -> dict:
    x = nse_cross_section(as_of)
    if x.empty:
        return {"decision_date":as_of,"status":"NO_PRICE_UNIVERSE","eligible":0}
    for c in ["ret_63d","ret_126d","avg_turnover_60d","close"]:
        x[c] = pd.to_numeric(x.get(c), errors="coerce")
    x = x[(x.avg_turnover_60d >= 2_000_000) & x.ret_63d.notna() & x.ret_126d.notna() & x.close.gt(0)].copy()
    if len(x) < 50:
        return {"decision_date":as_of,"status":"UNIVERSE_TOO_SMALL","eligible":int(len(x))}
    # Percentile score; no forward data used in selection.
    x["score"] = .5*x.ret_63d.rank(pct=True) + .5*x.ret_126d.rank(pct=True)
    x["selected"] = x.score >= x.score.quantile(.90)
    fut = _future_prices(as_of)
    rows = []
    x["symbol_key"] = x.symbol.astype(str).str.upper()
    x_by_symbol = x.drop_duplicates("symbol_key").set_index("symbol_key")
    for symbol, g in fut.groupby("symbol", sort=False):
        if symbol not in x_by_symbol.index:
            continue
        g = g.sort_values("date")
        post = g[g.date > pd.Timestamp(as_of)].reset_index(drop=True)
        if post.empty:
            continue
        p0 = float(x_by_symbol.loc[symbol, "close"])
        if p0 <= 0: continue
        selected = bool(x_by_symbol.loc[symbol, "selected"])
        row = {"symbol":symbol,"selected":selected,"entry":p0}
        for label,n in HORIZONS.items():
            if len(post) >= n:
                row[label] = float(post.iloc[n-1].close/p0-1)
                path = np.r_[p0, post.iloc[:min(n,126)].close.to_numpy(float)]
                peaks = np.maximum.accumulate(path)
                dd = path/peaks-1
                if label == "6m_126_sessions":
                    row["max_drawdown_6m"] = float(np.nanmin(dd))
                # first hit based on post-entry closes, in sessions
                hit2 = np.flatnonzero(post.close.to_numpy(float) >= 2*p0)
                hit3 = np.flatnonzero(post.close.to_numpy(float) >= 3*p0)
                if label == "6m_126_sessions":
                    row["sessions_to_2x_6m"] = int(hit2[0]+1) if len(hit2) else None
                    row["sessions_to_3x_6m"] = int(hit3[0]+1) if len(hit3) else None
            else:
                row[label] = None
        rows.append(row)
    o = pd.DataFrame(rows)
    if o.empty:
        return {"decision_date":as_of,"status":"NO_MATURE_OUTCOMES","eligible":int(len(x))}
    selected = o[o.selected]
    universe = o
    out = {"decision_date":as_of,"status":"OK","eligible":int(len(x)),
           "outcome_symbols":int(len(o)),"selected_symbols":int(len(selected))}
    for label in HORIZONS:
        a = selected[label].dropna() if not selected.empty else pd.Series(dtype=float)
        b = universe[label].dropna()
        out[label] = {
            "selected_mean_return":float(a.mean()) if len(a) else None,
            "universe_mean_return":float(b.mean()) if len(b) else None,
            "excess_return":float(a.mean()-b.mean()) if len(a) and len(b) else None,
            "selected_median_return":float(a.median()) if len(a) else None,
            "positive_hit_rate":float((a>0).mean()) if len(a) else None,
            "two_x_incidence":float((a>=1).mean()) if len(a) else None,
            "three_x_incidence":float((a>=2).mean()) if len(a) else None,
        }
    out["selected_mean_max_drawdown_6m"] = float(selected.max_drawdown_6m.mean()) if not selected.empty and selected.max_drawdown_6m.notna().any() else None
    out["selected_2x_within_6m"] = float(selected.sessions_to_2x_6m.notna().mean()) if not selected.empty else None
    out["selected_3x_within_6m"] = float(selected.sessions_to_3x_6m.notna().mean()) if not selected.empty else None
    out["median_sessions_to_2x_when_hit"] = float(selected.sessions_to_2x_6m.dropna().median()) if not selected.empty and selected.sessions_to_2x_6m.notna().any() else None
    out["median_sessions_to_3x_when_hit"] = float(selected.sessions_to_3x_6m.dropna().median()) if not selected.empty and selected.sessions_to_3x_6m.notna().any() else None
    # Recall: selected names that doubled within six months / all universe names that doubled.
    winners = universe[universe["6m_126_sessions"].notna() & universe["6m_126_sessions"].ge(1)]
    out["six_month_2x_winner_recall"] = float(winners.selected.mean()) if not winners.empty else None
    out["six_month_2x_winners_in_universe"] = int(len(winners))
    out["six_month_2x_winners_selected"] = int(winners.selected.sum()) if not winners.empty else 0
    return out


def _cluster_bootstrap(rows, metric, baseline, n=BOOTSTRAPS):
    df = pd.DataFrame(rows)
    valid = df.dropna(subset=[metric, baseline])
    if valid.empty:
        return {"status":"NO_DATA"}
    diffs = (valid[metric]-valid[baseline]).to_numpy(float)
    rng = np.random.default_rng(SEED)
    sims = np.empty(n)
    for i in range(n):
        idx = rng.integers(0,len(diffs),len(diffs))
        sims[i] = np.mean(diffs[idx])
    return {"decision_dates":int(len(diffs)),"mean_excess_return":float(diffs.mean()),
            "ci95_low":float(np.quantile(sims,.025)),"ci95_high":float(np.quantile(sims,.975)),
            "method":"decision-date cluster bootstrap; percentile 95% CI"}


def main():
    results=[]
    for i,d in enumerate(DATES,1):
        try:
            r=_evaluate(d)
        except Exception as e:
            r={"decision_date":d,"status":"ERROR","error":type(e).__name__+":"+str(e)[:300]}
        results.append(r)
        print(f"date={d} status={r.get('status')} selected={r.get('selected_symbols')} eligible={r.get('eligible')}",flush=True)
    ok=[r for r in results if r.get("status")=="OK"]
    combined={}
    for label in HORIZONS:
        combined[label]={}
        for key in ["selected_mean_return","universe_mean_return","excess_return","selected_median_return","positive_hit_rate","two_x_incidence","three_x_incidence"]:
            vals=[r[label].get(key) for r in ok if isinstance(r.get(label),dict) and r[label].get(key) is not None]
            combined[label][key]={"mean_across_dates":float(np.mean(vals)) if vals else None,"n_dates":len(vals)}
        # Bootstrap top-decile minus universe average return, by date.
        combined[label]["excess_return_ci95"]=_cluster_bootstrap(
            [{"a":r[label].get("selected_mean_return"),"b":r[label].get("universe_mean_return")} for r in ok if isinstance(r.get(label),dict)],
            "a","b")
    recall=[r["six_month_2x_winner_recall"] for r in ok if r.get("six_month_2x_winner_recall") is not None]
    out={
      "status":"PRICE_ONLY_FALSIFICATION_COMPLETE" if ok else "PRICE_ONLY_FALSIFICATION_NO_VALID_DATES",
      "method":"Blind top-decile price-only momentum (50% 63-session return rank + 50% 126-session return rank), liquid universe avg turnover >= INR 2m/day; outcomes measured after each decision date; no fundamental data or winner labels used for ranking.",
      "source":"Free TejHQ Indian Markets NSE daily parquet",
      "decision_dates_requested":DATES,"valid_decision_dates":len(ok),
      "failed_or_unmatured_dates":[r for r in results if r.get("status")!="OK"],
      "bootstrap_iterations":BOOTSTRAPS,"confidence_interval_method":"decision-date cluster bootstrap, 95% percentile interval",
      "six_month_2x_winner_recall_mean_across_dates":float(np.mean(recall)) if recall else None,
      "horizon_summary":combined,
      "per_date_results":results,
      "limitations":["This falsifies a price-only proxy, not the full fundamental MBE.","Survivorship/delisting coverage depends on the source dataset; no absent symbol is imputed.","Simple price returns exclude dividends, costs, taxes and slippage.","24-month outcomes are reported only for dates with enough subsequent daily observations."]
    }
    Path("reports").mkdir(exist_ok=True)
    Path("reports/price-only-historical-falsification.json").write_text(json.dumps(out,indent=2,default=str))
    print(json.dumps(out,indent=2,default=str))

if __name__=="__main__": main()
