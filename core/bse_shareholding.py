"""BSE LODR Reg.31 shareholding PIT adapter."""
from __future__ import annotations
import re, time
from typing import Any
import requests
from lxml import html
BASE_API="https://api.bseindia.com/BseIndiaAPI/api"
BASE_WEB="https://www.bseindia.com"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

def session():
    s=requests.Session()
    s.headers.update({"User-Agent":UA,"Referer":"https://www.bseindia.com/","Accept":"application/json, text/plain, */*"})
    return s

def lookup_code(s, symbol):
    symbol=str(symbol).strip().upper()
    r=s.get(f"{BASE_API}/PeerSmartSearch/w",params={"Type":"SS","text":symbol},timeout=20)
    r.raise_for_status(); txt=r.text.replace("&nbsp;"," ")
    for line in txt.splitlines():
        if symbol in line.upper():
            m=re.search(r"\b(\d{6})\b",line)
            if m: return m.group(1)
    if symbol in txt.upper():
        codes=re.findall(r"\b\d{6}\b",txt)
        return codes[-1] if codes else None
    return None

def filing_index(s, code):
    r=s.get(f"{BASE_API}/Corp_Shareholding_ng/w",params={"scripcode":code,"flag":"0","indtype":""},timeout=30)
    r.raise_for_status()
    if "application/json" not in r.headers.get("Content-Type","").lower():
        raise RuntimeError("BSE shareholding endpoint returned non-JSON content")
    return r.json().get("Table") or []

def parse_ixbrl(blob):
    root=html.fromstring(blob); ns={"ix":"http://www.xbrl.org/2013/inlineXBRL"}; contexts={}
    for node in root.xpath("//ix:nonNumeric | //ix:nonFraction",namespaces=ns):
        name=node.get("name",""); cref=node.get("contextRef","")
        if not name or not cref: continue
        value=" ".join(" ".join(node.itertext()).split())
        contexts.setdefault(cref,[]).append((name,value))
    rows=[]
    for cref,vals in contexts.items():
        names=" | ".join(n for n,_ in vals)
        if "NameOfTheShareHolders" not in names: continue
        row={"context_ref":cref}
        for n,v in vals:
            if n.endswith("NameOfTheShareHolders"): row["holder_name"]=v
            elif n.endswith("NumberOfShares"): row["shares"]=v
            elif n.endswith("ShareholdingPercentage"): row["holding_pct"]=v
            elif n.endswith("PAN"): row["pan"]=v
        if row.get("holder_name"): rows.append(row)
    return rows

def fetch_symbol(s,symbol,quarters=4,sleep_s=0.75):
    out={"symbol":symbol.upper(),"bse_code":None,"status":"FAILED","quarters":[]}
    code=lookup_code(s,symbol); out["bse_code"]=code
    if not code: out["status"]="NO_BSE_CODE"; return out
    filings=filing_index(s,code)
    usable=[x for x in filings if x.get("IsXBRL") and x.get("XBRLAttachment")][:quarters]
    out["filings"]=len(filings); out["ixbrl_candidates"]=len(usable)
    for f in usable:
        rec={"quarter":f.get("sQtrName"),"period_end":f.get("EndDate"),"broadcast_time":f.get("broadcastTime"),"holders":[]}
        try:
            rr=s.get(BASE_WEB+f["XBRLAttachment"],timeout=45); rr.raise_for_status()
            rec["holders"]=parse_ixbrl(rr.content); rec["http_bytes"]=len(rr.content)
        except Exception as e: rec["error"]=type(e).__name__+":"+str(e)[:160]
        out["quarters"].append(rec); time.sleep(sleep_s)
    out["status"]="PASS" if any(q.get("holders") for q in out["quarters"]) else "NO_HOLDER_ROWS"
    return out
