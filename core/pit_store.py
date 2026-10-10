"""Persistent read-only PIT feature store."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

def load_facts(as_of: str, root="data/pit/facts") -> pd.DataFrame:
    files=sorted(Path(root).glob("*.jsonl"))
    rows=[]
    cutoff=pd.Timestamp(as_of+" 23:59:59")
    for p in files:
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip(): continue
                x=json.loads(line)
                a=pd.to_datetime(x.get("available_at"),errors="coerce")
                p=pd.to_datetime(x.get("period_end"),errors="coerce")
                # PIT eligibility requires both public availability and the
                # accounting period itself to be no later than the decision date.
                if pd.notna(a) and a<=cutoff and pd.notna(p) and p<=cutoff:
                    rows.append(x)
        except Exception:
            continue
    if not rows: return pd.DataFrame()
    df=pd.DataFrame(rows)
    for c in ("available_at","period_end","period_start"):
        if c in df: df[c]=pd.to_datetime(df[c],errors="coerce")
    return df

def coverage(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"symbols":0,"facts":0,"core_symbols":0}
    core={"revenue","pat","ebitda","debt","equity","cfo","eps"}
    counts=df[df.metric.isin(core)].groupby("symbol").metric.nunique()
    return {"symbols":int(df.symbol.nunique()),"facts":int(len(df)),"core_symbols":int((counts>=5).sum())}
