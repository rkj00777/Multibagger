"""BSE quarterly shareholding PIT trust probe.

Purpose:
- Resolve NSE-style symbols to BSE scrip codes.
- Fetch BSE LODR Reg.31 quarterly filing index.
- Download the latest N iXBRL filings that are available.
- Extract structured mutual-fund holder facts where present.
- Emit a machine-readable trust artifact.

This is DATA INFRASTRUCTURE ONLY. It does not feed MBE scores or promotion gates.
"""
from __future__ import annotations
import argparse, json, re, time
from datetime import datetime
from pathlib import Path
import requests
from lxml import html

BASE_API = "https://api.bseindia.com/BseIndiaAPI/api"
BASE_WEB = "https://www.bseindia.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

def session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Referer": "https://www.bseindia.com/",
        "Accept": "application/json, text/plain, */*",
    })
    return s

def lookup_code(s, symbol):
    r = s.get(f"{BASE_API}/PeerSmartSearch/w", params={"Type":"SS","text":symbol}, timeout=20)
    r.raise_for_status()
    text = r.text.replace("&nbsp;", " ")
    pat = rf"<\\w+>{re.escape(symbol.upper())}</\\w+>\\s+\\w+\\s+(\\d{{6}})"
    m = re.search(pat, text)
    if m:
        return m.group(1)
    # Fallback: identify a six-digit code in the same response as an exact symbol token.
    if symbol.upper() in text.upper():
        codes = re.findall(r"\\b\\d{6}\\b", text)
        if codes:
            return codes[-1]
    return None

def filing_index(s, code):
    r = s.get(
        f"{BASE_API}/Corp_Shareholding_ng/w",
        params={"scripcode":code, "flag":"0", "indtype":""},
        timeout=30,
    )
    r.raise_for_status()
    if "application/json" not in r.headers.get("Content-Type","").lower():
        raise RuntimeError("BSE shareholding endpoint returned non-JSON content")
    data = r.json()
    return data.get("Table") or []

def parse_ixbrl(blob):
    root = html.fromstring(blob)
    ns = {"ix":"http://www.xbrl.org/2013/inlineXBRL"}
    facts = {}
    for node in root.xpath("//ix:nonNumeric | //ix:nonFraction", namespaces=ns):
        name = node.get("name","")
        cref = node.get("contextRef","")
        if not name or not cref:
            continue
        text = " ".join(" ".join(node.itertext()).split())
        facts.setdefault(cref, []).append({"name":name,"value":text})
    rows = []
    for cref, vals in facts.items():
        joined = " | ".join(v["name"] for v in vals)
        if "NameOfTheShareHolders" not in joined:
            continue
        row = {"context_ref":cref}
        for v in vals:
            n = v["name"]
            if n.endswith("NameOfTheShareHolders"): row["holder_name"]=v["value"]
            elif n.endswith("NumberOfShares"): row["shares"]=v["value"]
            elif n.endswith("ShareholdingPercentage"): row["holding_pct"]=v["value"]
            elif n.endswith("PAN"): row["pan"]=v["value"]
        rows.append(row)
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="RELIANCE,TCS,ICICIBANK,HDFCBANK,INFY,SBIN,LT,TEGA,AEQUS,ROLEXRINGS")
    ap.add_argument("--quarters", type=int, default=4)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--out", default="reports/bse-shareholding-trust.json")
    args = ap.parse_args()
    symbols=[x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    s=session()
    results=[]
    for symbol in symbols:
        item={"symbol":symbol,"bse_code":None,"status":"FAILED","filings":0,"ixbrl_filings":0,"holder_rows":0,"quarters":[]}
        try:
            code=lookup_code(s,symbol)
            item["bse_code"]=code
            if not code:
                item["status"]="NO_BSE_CODE"
                results.append(item); continue
            rows=filing_index(s,code)
            item["filings"]=len(rows)
            usable=[x for x in rows if x.get("IsXBRL") and x.get("XBRLAttachment")][:args.quarters]
            for f in usable:
                rec={"quarter":f.get("sQtrName"),"period_end":f.get("EndDate"),"broadcast_time":f.get("broadcastTime"),"holders":[]}
                try:
                    u=f["XBRLAttachment"]
                    rr=s.get(BASE_WEB+u,timeout=45)
                    rr.raise_for_status()
                    holders=parse_ixbrl(rr.content)
                    rec["holders"]=holders
                    rec["http_bytes"]=len(rr.content)
                    item["ixbrl_filings"]+=1
                    item["holder_rows"]+=len(holders)
                except Exception as e:
                    rec["error"]=type(e).__name__+":"+str(e)[:160]
                item["quarters"].append(rec)
                time.sleep(args.sleep)
            item["status"]="PASS" if item["ixbrl_filings"] else "NO_IXBRL"
        except Exception as e:
            item["error"]=type(e).__name__+":"+str(e)[:200]
        results.append(item)
    passed=sum(x["status"]=="PASS" for x in results)
    out={"schema":"bse-shareholding-trust-v1","generated_at":datetime.utcnow().isoformat()+"Z",
         "source":"BSE LODR Reg.31 iXBRL","pit_rule":"use broadcast_time <= requested as-of before production use",
         "production_status":"TRUST_PROBE_ONLY","symbols_requested":len(symbols),"symbols_passed":passed,
         "results":results}
    p=Path(args.out); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(out,indent=2,sort_keys=True),encoding="utf-8")
    print(json.dumps({"status":"BSE_SHAREHOLDING_TRUST_PROBE_COMPLETE","symbols_requested":len(symbols),"symbols_passed":passed,"artifact":str(p)},indent=2))
    if passed == 0:
        raise SystemExit(2)

if __name__=="__main__":
    main()
