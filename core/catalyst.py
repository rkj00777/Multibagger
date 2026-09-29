import requests,re
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed
BASE="https://www.nseindia.com"
URL=BASE+"/api/corporate-announcements"
class NSECatalyst:
    def __init__(self,workers=8):
        self.s=requests.Session(); self.s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*","Referer":BASE+"/"})
        self.workers=workers
        try:self.s.get(BASE+"/",timeout=15)
        except:pass
    def one(self,symbol,as_of):
        try:
            d=datetime.strptime(as_of,"%Y-%m-%d"); p={"index":"equities","symbol":symbol,"from_date":(d-timedelta(days=30)).strftime("%d-%m-%Y"),"to_date":d.strftime("%d-%m-%Y"),"page":1,"size":50}
            r=self.s.get(URL,params=p,timeout=15)
            if not r.ok:return []
            data=r.json()
            rows=data.get("data",data if isinstance(data,list) else [])
            return [{**x,"symbol":x.get("symbol") or x.get("sym") or symbol} if isinstance(x,dict) else {"symbol":symbol,"text":x} for x in rows]
        except:return []
    def batch(self,symbols,as_of):
        out=[]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            fut={ex.submit(self.one,s,as_of):s for s in symbols}
            for f in as_completed(fut):
                try:
                    for x in f.result(): out.append(x)
                except:pass
        return out

def catalyst_score(items):
    positive=("order","capacity","expansion","commission","approval","contract","fund","acquisition","export","product","award","partnership")
    negative=("resign","default","fraud","pledge","downgrade","delay","fire","insolvency","penalty","search","investigation")
    text=" ".join(str(x).lower() for x in items)
    p=sum(text.count(k) for k in positive); n=sum(text.count(k) for k in negative)
    return max(0,min(100,50+10*p-15*n))


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
            # Restrict the catalyst scan to the public Documents/Announcements area when possible.
            m=re.search(r"Announcements(.*?)Annual reports",text,re.I|re.S)
            block=m.group(1) if m else text[-12000:]
            return [{"symbol":symbol,"text":block,"source_type":"SCREENER_PUBLIC_FALLBACK","source_url":u}]
        except:return []
    def batch(self,symbols,as_of):
        out=[]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            fut={ex.submit(self.one,s,as_of):s for s in symbols}
            for f in as_completed(fut):
                try: out.extend(f.result())
                except: pass
        return out
