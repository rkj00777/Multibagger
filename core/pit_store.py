"""Read-only PIT feature store helpers."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

def load_snapshot(as_of: str, root="data/pit") -> pd.DataFrame:
    p = Path(root) / f"fundamentals_{as_of}.jsonl"
    if not p.exists():
        return pd.DataFrame()
    rows=[json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows: return pd.DataFrame()
    df=pd.DataFrame(rows)
    df["available_at"]=pd.to_datetime(df["available_at"],errors="coerce")
    df["period_end"]=pd.to_datetime(df["period_end"],errors="coerce")
    cutoff=pd.Timestamp(as_of+" 23:59:59")
    return df[(df.available_at.notna()) & (df.available_at<=cutoff)].copy()

def coverage(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"symbols":0,"facts":0,"core_symbols":0}
    core={"revenue","pat","ebitda","debt","equity","cfo","eps"}
    counts=df[df.metric.isin(core)].groupby("symbol").metric.nunique()
    return {
        "symbols":int(df.symbol.nunique()),
        "facts":int(len(df)),
        "core_symbols":int((counts>=5).sum()),
    }
