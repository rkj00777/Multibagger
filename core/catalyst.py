import requests,re\nfrom datetime import datetime,timedelta
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
            return data.get("data",data if isinstance(data,list) else [])
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
