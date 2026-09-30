import os,json,datetime,sys
from multibagger.engine import run

as_of=os.getenv("AS_OF") or datetime.date.today().isoformat()
os.makedirs("reports",exist_ok=True)
out=run(as_of)
json.dump(out,open("reports/multibagger-live.json","w"),indent=2,default=str)
print(json.dumps(out,indent=2,default=str))
if out.get("status") != "LIVE_SCAN_COMPLETE":
    sys.exit(2)
