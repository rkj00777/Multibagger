"""Build the exchange-first PIT fundamental snapshot for the liquid universe."""
import argparse, json, os
from datetime import date
from core.market_data import nse_cross_section
from core.nse_pit import NSEPIT, snapshot, write_snapshot

p = argparse.ArgumentParser()
p.add_argument("--as-of", default=date.today().isoformat())
p.add_argument("--limit", type=int, default=300)
args = p.parse_args()

df = nse_cross_section(args.as_of)
symbols = (df.sort_values(["avg_turnover_60d","turnover"], ascending=False)
             .head(args.limit)["symbol"].dropna().astype(str).str.upper().tolist())

client = NSEPIT()
facts=[]
for i,s in enumerate(symbols,1):
    facts.extend(client.facts(s,args.as_of))
    if i % 25 == 0:
        print(f"processed={i}/{len(symbols)} facts={len(facts)}", flush=True)

facts=snapshot(facts,args.as_of)
path=write_snapshot(facts,args.as_of)
print(json.dumps({
    "status":"PIT_INGEST_COMPLETE",
    "as_of":args.as_of,
    "symbols_requested":len(symbols),
    "facts":len(facts),
    "path":path,
    "source":"NSE_XBRL",
},indent=2))
