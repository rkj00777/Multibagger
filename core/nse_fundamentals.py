import json,re,html,xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from bs4 import BeautifulSoup

BASE="https://www.nseindia.com"
API=BASE+"/api/integrated-filing-results"
LEGACY=BASE+"/api/corporates-financial-results"
ALIASES={
"revenue":["RevenueFromOperations","Revenue","IncomeFromOperations","Turnover"],
"pat":["ProfitLoss","ProfitForThePeriod","ProfitAfterTax","NetProfit"],
"ebit":["EBIT","EarningsBeforeInterestAndTax","OperatingProfit"],
"ebitda":["EBITDA","EarningsBeforeInterestTaxDepreciationAndAmortisation"],
"cfo":["CashFlowsFromUsedInOperatingActivities","NetCashGeneratedFromOperatingActivities","CashGeneratedFromOperations"],
"capex":["PurchaseOfPropertyPlantAndEquipment","PaymentsToAcquirePropertyPlantAndEquipment","PurchaseOfTangibleAssets"],
"debt":["Borrowings","Debt","BorrowingsCurrentAndNonCurrent"],
"cash":["CashAndCashEquivalents","CashAndBankBalances","Cash"],
"equity":["Equity","EquityAttributableToOwnersOfParent","ShareholdersEquity"],
"interest":["FinanceCosts","InterestExpense","FinanceCost"],
"shares":["NumberOfSharesOutstanding","EquitySharesOutstanding"],
"eps":["BasicEarningsLossPerShare","BasicEarningsPerShare","DilutedEarningsPerShare"]}

def dt(v):
    if not v:return None
    s=str(v).strip().replace("Z","+00:00")
    for f in ("%d-%b-%Y %H:%M:%S","%d-%b-%Y","%Y-%m-%dT%H:%M:%S%z","%Y-%m-%d"):
        try:return datetime.strptime(s,f).replace(tzinfo=None)
        except: pass
    try:return datetime.fromisoformat(s).replace(tzinfo=None)
    except:return None

def num(v):
    try:return float(Decimal(str(v).replace(",","")))
    except:return None

def norm_url(v):
    if not isinstance(v,str): return None
    v=v.strip()
    if v.startswith("//"): return "https:"+v
    if v.startswith("/"): return BASE+v
    return v if v.startswith("http") else None

def walk(o):
    if isinstance(o,dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)

def filing_rows(payload):
    out=[]
    for d in walk(payload):
        low={str(k).lower():v for k,v in d.items()}
        u=None
        for k,v in low.items():
            if "xbrl" in k:
                u=norm_url(v)
                if u: break
        if not u:
            for v in low.values():
                u=norm_url(v)
                if u and ("xbrl" in u.lower() or "ixbrl" in u.lower()): break
        if not u: continue
        symbol=next((v for k,v in low.items() if k in ("symbol","sym")),None)
        isin=next((v for k,v in low.items() if k in ("isin","sm_isin","isinno")),None)
        pe=next((v for k,v in low.items() if k in ("periodend","period_end","quarterend","quarter_end","todate","to_date","enddate","end_date","periodenddate","period_end_date")),None)
        if pe is None: pe=next((v for k,v in low.items() if "period" in k and "end" in k),None)
        avail=next((dt(v) for k,v in low.items() if k in ("exchdisstime","exchangedisseminationtime","broadcastdatetime","broadcastdate","broadcast_date","filingdatetime","filingdate") and dt(v)),None)
        if u and (symbol or isin):
            out.append({"symbol":symbol,"isin":isin,"period_end":dt(pe) if pe else None,"available_at":avail,"xbrl_url":u,"raw":d})
    ded={}
    for r in out: ded[(r["symbol"],r["xbrl_url"])]=r
    return list(ded.values())

def local(tag): return tag.rsplit("}",1)[-1] if isinstance(tag,str) else ""

def contexts(root):
    c={}
    for x in root.findall(".//{*}context"):
        i=x.attrib.get("id")
        if not i: continue
        a=x.find(".//{*}instant"); s=x.find(".//{*}startDate"); e=x.find(".//{*}endDate")
        c[i]={"instant":dt(a.text if a is not None else None),"start":dt(s.text if s is not None else None),"end":dt(e.text if e is not None else None)}
    return c

def metric(tag):
    t=local(tag).lower()
    for m,als in ALIASES.items():
        if any(a.lower() in t for a in als): return m
    return None

def parse_xbrl(raw,filing):
    text=raw.decode("utf-8","replace") if isinstance(raw,bytes) else str(raw)
    text=re.sub(r"&([A-Za-z][A-Za-z0-9]+);",lambda m: html.entities.html5.get(m.group(1)+";",""),text).encode()
    try: root=ET.fromstring(text)
    except:
        soup=BeautifulSoup(text,"lxml-xml"); root=ET.fromstring(str(soup).encode())
    ctx=contexts(root); rows=[]
    for f in root.iter():
        m=metric(f.tag); v=num(f.text)
        if not m or v is None: continue
        c=ctx.get(f.attrib.get("contextRef"),{})
        pe=c.get("end") or c.get("instant")
        if not pe: continue
        rows.append({"metric":m,"value":v,"period_end":pe.date().isoformat(),"period_start":c.get("start").date().isoformat() if c.get("start") else None,"duration_days":(c["end"]-c["start"]).days if c.get("end") and c.get("start") else 0,"available_at":filing.get("available_at").isoformat() if filing.get("available_at") else None,"source_url":filing["xbrl_url"],"source_type":"NSE_XBRL","symbol":filing.get("symbol"),"isin":filing.get("isin")})
    ded={(r["metric"],r["period_end"],r["symbol"]):r for r in rows}
    return list(ded.values())

class NSEFundamentals:
    def __init__(self,workers=8):
        self.workers=workers
        self.s=requests.Session()
        self.s.headers.update({"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36","Accept":"application/json, text/plain, */*","Referer":BASE+"/","Connection":"keep-alive"})
        try:self.s.get(BASE+"/",timeout=15)
        except:pass
    def catalog(self,symbol,as_of,page_size=100):
        # Pre-2025 snapshots use NSE legacy Financial Results; 2025+ uses Integrated Filing.
        if str(as_of)[:4] < "2025":
            p={"index":"equities","period":"Quarterly","page":1,"size":page_size,"symbol":symbol}
            try:
                rr=self.s.get(LEGACY,params=p,timeout=20)
                if rr.ok:
                    rows=filing_rows(rr.json()); cutoff=dt(as_of+" 23:59:59")
                    rows=[x for x in rows if x.get("available_at") is not None and x["available_at"]<=cutoff]
                    rows.sort(key=lambda x:(x.get("period_end") or datetime.min,x.get("available_at") or datetime.min),reverse=True)
                    return rows
            except: pass
            return []
        p={"type":"Integrated Filing- Financials","page":1,"size":page_size,"index":"equities","period_ended":"all","symbol":symbol}
        try:
            r=self.s.get(API,params=p,timeout=20)
            if r.ok:
                rows=filing_rows(r.json())
                cutoff=dt(as_of+" 23:59:59")
                rows=[x for x in rows if x.get("available_at") is not None and x["available_at"]<=cutoff]
                rows.sort(key=lambda x:(x.get("period_end") or datetime.min,x.get("available_at") or datetime.min),reverse=True)
                return rows
        except: pass
        return []
    def one(self,symbol,as_of):
        rows=self.catalog(symbol,as_of)
        # Current filing feed is primary. Retrieve only the latest 6 available filings.
        facts=[]
        for f in rows[:6]:
            try:
                r=self.s.get(f["xbrl_url"],timeout=20)
                if r.ok: facts.extend(parse_xbrl(r.content,f))
            except: continue
        return facts
    def batch(self,symbols,as_of):
        out=[]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            fut={ex.submit(self.one,s,as_of):s for s in symbols}
            for f in as_completed(fut):
                try: out.extend(f.result())
                except: pass
        return out
