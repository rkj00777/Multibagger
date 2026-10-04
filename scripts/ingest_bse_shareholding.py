"""Incremental BSE shareholding PIT ingestion. Data layer only."""
from __future__ import annotations
import argparse,json
from datetime import datetime
from pathlib import Path
from core.market_data import nse_cross_section
from core.bse_shareholding import session,fetch_symbol
ap=argparse.ArgumentParser()
ap.add_argument("--as-of",default=datetime.utcnow().date().isoformat())
ap.add_argument("--limit",type=int,default=300); ap.add_argument("--offset",type=int,default=0)
ap.add_argument("--quarters",type=int,default=4); ap.add_argument("--sleep",type=float,default=0.75)
ap.add_argument("--out",default="data/pit/shareholding/bse.jsonl"); a=ap.parse_args()
df=nse_cross_section(a.as_of)
universe=df.sort_values(["avg_turnover_60d","ret_126d"],ascending=False).drop_duplicates("symbol")
symbols=universe.iloc[a.offset:a.offset+a.limit]["symbol"].dropna().astype(str).str.upper().tolist()
s=session(); rows=[]; passed=0
for i,symbol in enumerate(symbols,1):
    try:
        item=fetch_symbol(s,symbol,a.quarters,a.sleep)
        if item.get("status")=="PASS": passed+=1
        for q in item.get("quarters",[]):
            if not q.get("broadcast_time"): continue
            for h in q.get("holders",[]):
                rows.append({"symbol":symbol,"bse_code":item.get("bse_code"),"quarter":q.get("quarter"),
                             "period_end":q.get("period_end"),"broadcast_time":q.get("broadcast_time"),
                             **h,"source":"BSE_LODR_IXBRL"})
    except Exception: pass
    if i%25==0: print(f"processed={i}/{len(symbols)} rows={len(rows)} passed={passed}",flush=True)
p=Path(a.out); p.parent.mkdir(parents=True,exist_ok=True); existing={}
if p.exists():
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            x=json.loads(line); existing[(x.get("symbol"),x.get("quarter"),x.get("holder_name"),x.get("shares"),x.get("holding_pct"),x.get("broadcast_time"))]=x
for x in rows:
    existing[(x.get("symbol"),x.get("quarter"),x.get("holder_name"),x.get("shares"),x.get("holding_pct"),x.get("broadcast_time"))]=x
ordered=sorted(existing.values(),key=lambda z:(z.get("broadcast_time",""),z.get("symbol",""),z.get("holder_name","")))
p.write_text("\n".join(json.dumps(x,separators=(",",":"),sort_keys=True) for x in ordered)+"\n",encoding="utf-8")
Path("data/pit/shareholding/manifest.json").write_text(json.dumps({"as_of":a.as_of,"offset":a.offset,"requested":len(symbols),"new_or_seen_rows":len(rows),"total_rows":len(existing),"source":"BSE_LODR_IXBRL","pit_rule":"broadcast_time <= as_of"},indent=2,sort_keys=True),encoding="utf-8")
print(json.dumps({"status":"BSE_SHAREHOLDING_INGEST_COMPLETE","as_of":a.as_of,"requested":len(symbols),"rows":len(rows),"total_rows":len(existing),"passed_symbols":passed,"offset":a.offset},indent=2))
