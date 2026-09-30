"""Incrementally ingest exchange-first PIT fundamentals from NSE.

Only normalized facts are persisted. Revisions remain distinct because
available_at is part of the identity, allowing historical reconstruction.
"""
import argparse, json, hashlib, os
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.market_data import nse_cross_section
from core.nse_pit import NSEPIT

p=argparse.ArgumentParser()
p.add_argument("--as-of",default=date.today().isoformat())
p.add_argument("--limit",type=int,default=300)
args=p.parse_args()

df=nse_cross_section(args.as_of)
symbols=(df.sort_values(["avg_turnover_60d","discovery_score"],ascending=False)
           .head(args.limit)["symbol"].dropna().astype(str).str.upper().tolist())

client=NSEPIT()
facts=[]
workers=min(6,max(1,int(os.getenv("PIT_WORKERS","6"))))
with ThreadPoolExecutor(max_workers=workers) as ex:
    fut={ex.submit(client.facts,s,args.as_of):s for s in symbols}
    for i,fut_item in enumerate(as_completed(fut),1):
        try: facts.extend(fut_item.result())
        except Exception: pass
        if i%25==0: print(f"processed={i}/{len(symbols)} facts={len(facts)}",flush=True)

# Persist by availability month; this is an append-only PIT evidence store.
root=Path("data/pit/facts"); root.mkdir(parents=True,exist_ok=True)
groups={}
for r in facts:
    a=r.get("available_at")
    if not a: continue
    groups.setdefault(str(a)[:7],[]).append(r)

written=0
for month,rows in groups.items():
    path=root/f"{month}.jsonl"
    existing={}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            x=json.loads(line)
            key=(x.get("symbol"),x.get("metric"),x.get("period_end"),x.get("period_start"),x.get("available_at"),x.get("value"),x.get("source_url"))
            existing[key]=x
    for x in rows:
        key=(x.get("symbol"),x.get("metric"),x.get("period_end"),x.get("period_start"),x.get("available_at"),x.get("value"),x.get("source_url"))
        existing[key]=x
    ordered=sorted(existing.values(),key=lambda x:(x.get("available_at",""),x.get("symbol",""),x.get("metric",""),x.get("period_end","")))
    path.write_text("\n".join(json.dumps(x,separators=(",",":"),sort_keys=True) for x in ordered)+"\n",encoding="utf-8")
    written+=len(rows)

manifest=Path("data/pit/manifest.json")
manifest.write_text(json.dumps({
    "as_of":args.as_of,
    "symbols_requested":len(symbols),
    "facts_observed":len(facts),
    "source":"NSE_XBRL",
    "pit_rule":"available_at <= as_of",
    "months":sorted(p.name for p in root.glob("*.jsonl"))
},indent=2,sort_keys=True))
print(json.dumps({"status":"PIT_INGEST_COMPLETE","as_of":args.as_of,"symbols_requested":len(symbols),"facts_observed":len(facts),"facts_written_or_seen":written,"source":"NSE_XBRL"},indent=2))
