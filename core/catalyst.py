import requests,re
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed

BASE="https://www.nseindia.com"
URL=BASE+"/api/corporate-announcements"

POSITIVE=("order","orders","capacity","expansion","commission","approval","contract","fund","acquisition","export","product","award","partnership","capex","facility","investment","inaugurat","production","launch")
NEGATIVE=("resign","default","fraud","pledge","downgrade","delay","fire","insolvency","penalty","search","investigation","forensic","warning","loss of control")
NEUTRAL=("result","board meeting","dividend","annual general meeting","credit rating")

class NSECatalyst:
    def __init__(self,workers=8):
        self.s=requests.Session()
        self.s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*","Referer":BASE+"/"})
        self.workers=workers
        try:self.s.get(BASE+"/",timeout=15)
        except:pass

    def one(self,symbol,as_of):
        try:
            d=datetime.strptime(as_of,"%Y-%m-%d")
            p={"index":"equities","symbol":symbol,
               "from_date":(d-timedelta(days=45)).strftime("%d-%m-%Y"),
               "to_date":d.strftime("%d-%m-%Y"),"page":1,"size":50}
            r=self.s.get(URL,params=p,timeout=15)
            if not r.ok:return []
            data=r.json()
            rows=data.get("data",data if isinstance(data,list) else [])
            out=[]
            for x in rows:
                if isinstance(x,dict):
                    y=dict(x)
                    y["symbol"]=y.get("symbol") or y.get("sym") or symbol
                    y["source_type"]="NSE_CORPORATE_ANNOUNCEMENT"
                    out.append(y)
                else:
                    out.append({"symbol":symbol,"text":str(x),"source_type":"NSE_CORPORATE_ANNOUNCEMENT"})
            return out
        except:return []

    def batch(self,symbols,as_of):
        out=[]
        symbols=[str(s).upper() for s in symbols]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            fut={ex.submit(self.one,s,as_of):s for s in symbols}
            for f in as_completed(fut):
                try: out.extend(f.result() or [])
                except:pass

        # NSE can be blocked/rate-limited from hosted runners. Fill only missing
        # symbols from the public Screener announcements page; this is discovery
        # evidence, never a substitute for PIT fundamentals.
        got={str(x.get("symbol")).upper() for x in out if x.get("symbol")}
        missing=[s for s in symbols if s not in got]
        if missing:
            try:
                fallback=ScreenerCatalyst(workers=min(4,len(missing))).batch(missing,as_of)
                out.extend(fallback)
            except Exception:pass
        return out

def _item_text(item):
    if isinstance(item,dict):
        vals=[]
        for k in ("text","desc","description","subject","title","headline","details","attchmntText","announcement","purpose"):
            if item.get(k) is not None: vals.append(str(item.get(k)))
        return " ".join(vals)
    return str(item)

def catalyst_score(items):
    if not items:return 50.0
    positive=negative=0
    evidence=0
    for item in items:
        text=_item_text(item).lower()
        if not text.strip():continue
        evidence+=1
        positive += sum(text.count(k) for k in POSITIVE)
        negative += sum(text.count(k) for k in NEGATIVE)
    if evidence==0:return 50.0
    # Saturating evidence score: a genuine cluster of positive corporate events
    # can move the score materially, while repeated boilerplate cannot dominate.
    raw=50 + min(40,8*positive) - min(50,15*negative)
    return float(max(0,min(100,raw)))

class ScreenerCatalyst:
    def __init__(self,workers=8):
        self.workers=workers
        self.s=requests.Session()
        self.s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"text/html,application/xhtml+xml"})

    def one(self,symbol,as_of):
        try:
            from bs4 import BeautifulSoup
            u=f"https://www.screener.in/company/{symbol}/"
            r=self.s.get(u,timeout=10)
            if not r.ok:return []
            soup=BeautifulSoup(r.text,"html.parser")
            text=soup.get_text(" ",strip=True)
            # Prefer the public announcements/documents block. Limit text so
            # unrelated historical page content cannot manufacture a catalyst.
            m=re.search(r"Announcements(.*?)Annual reports",text,re.I|re.S)
            block=m.group(1) if m else text[-12000:]
            if not block.strip():return []
            return [{"symbol":symbol,"text":block,
                     "source_type":"SCREENER_PUBLIC_FALLBACK",
                     "source_url":u,"as_of":as_of}]
        except:return []

    def batch(self,symbols,as_of):
        out=[]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            fut={ex.submit(self.one,s,as_of):s for s in symbols}
            for f in as_completed(fut):
                try:out.extend(f.result() or [])
                except:pass
        return out
