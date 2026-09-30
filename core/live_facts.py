"""Provider-neutral live facts contract for the Multibagger engine.

The acquisition layer (Firecrawl, NSE, or another approved source) supplies
normalized facts. The scoring engine never depends on the acquisition client.
"""

from __future__ import annotations
from typing import Iterable, Mapping
import pandas as pd

REQUIRED_METRICS = {
    "revenue", "pat", "ebitda", "debt", "equity", "cfo", "fcf", "eps"
}


def normalize_facts(records: Iterable[Mapping]) -> list[dict]:
    out = []
    for r in records or []:
        x = dict(r)
        if not x.get("symbol") or not x.get("metric") or x.get("value") is None:
            continue
        x["symbol"] = str(x["symbol"]).upper().strip()
        x["metric"] = str(x["metric"]).lower().strip()
        x["period_end"] = pd.to_datetime(x.get("period_end"), errors="coerce")
        x["available_at"] = pd.to_datetime(x.get("available_at"), errors="coerce")
        if pd.isna(x["period_end"]):
            continue
        out.append(x)
    return out


def coverage(records: Iterable[Mapping]) -> dict:
    f = pd.DataFrame(normalize_facts(records))
    if f.empty:
        return {"symbols": 0, "facts": 0, "symbols_with_core_metrics": 0}
    core = f[f.metric.isin(REQUIRED_METRICS)]
    counts = core.groupby("symbol").metric.nunique()
    return {
        "symbols": int(f.symbol.nunique()),
        "facts": int(len(f)),
        "symbols_with_core_metrics": int((counts >= 5).sum()),
    }
