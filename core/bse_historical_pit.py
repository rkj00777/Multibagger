"""Independent BSE official-archive fallback for historical PIT financial facts.

This adapter uses BSE's public security-search, financial-result announcement
archive, dissemination timestamps and official XBRL/iXBRL attachments. It is
a second exchange source, not a current-fundamentals substitute. Every returned
fact retains the exchange availability timestamp and original filing URL.
"""
from __future__ import annotations
import json, time
from datetime import datetime
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup

BASE = "https://api.bseindia.com/BseIndiaAPI/api"
ANN = BASE + "/AnnSubCategoryGetData/w"
SEARCH = BASE + "/PeerSmartSearch/w"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/134.0 Safari/537.36"
ALIASES = {
 "revenue":("revenuefromoperations","revenue","income","turnover","income from operations"),
 "pat":("profitloss","profitfortheperiod","profitaftertax","netprofit","profitlossattributable"),
 "ebitda":("ebitda","earningsbeforeinteresttaxdepreciationandamortisation"),
 "cfo":("cashflowsfromusedinoperatingactivities","netcashgeneratedfromoperatingactivities","cashgeneratedfromoperations"),
 "capex":("purchaseofpropertyplantandequipment","paymentstoacquirepropertyplantandequipment","purchaseoftangibleassets"),
 "debt":("borrowings","debt","borrowingscurrentandnoncurrent","financialliabilities"),
 "cash":("cashandcashequivalents","cashandbankbalances"),
 "equity":("equityattributabletoownersofparent","shareholdersequity","equity"),
 "interest":("financecosts","interestexpense","financecost","interestexpended"),
 "eps":("basicearningsloss pershare".replace(" ",""),"basicearningspershare","dilutedeps","dilutedeps"),
}
FLOW={"revenue","pat","ebitda","cfo","capex","interest","eps"}

def _dt(v):
    if not v: return None
    s=str(v).strip().replace("Z","+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z","%Y-%m-%d %H:%M:%S","%d-%b-%Y %H:%M:%S","%d-%b-%Y %H:%M","%Y-%m-%d","%d-%b-%Y","%d/%m/%Y"):
        try: return datetime.strptime(s,fmt).replace(tzinfo=None)
        except ValueError: pass
    try:return datetime.fromisoformat(s).replace(tzinfo=None)
    except Exception:return None

def _metric(name):
    n=str(name).rsplit("}",1)[-1].replace("_","").lower()
    for metric, aliases in ALIASES.items():
        if any(a in n for a in aliases): return metric
    return None

class BSEHistoricalPIT:
    def __init__(self, timeout=25, delay=0.25, max_pages=8):
        self.timeout=timeout; self.delay=delay; self.max_pages=max_pages
        self.session=requests.Session()
        self.session.headers.update({"User-Agent":UA,"Accept":"application/json, text/plain, */*",
          "Referer":"https://www.bseindia.com/","X-Requested-With":"XMLHttpRequest",
          "Accept-Language":"en-IN,en;q=0.9"})
        self.codes={}; self.catalog_cache={}; self.doc_cache={}

    def _get(self,url,params=None):
        r=self.session.get(url,params=params,timeout=self.timeout)
        r.raise_for_status()
        time.sleep(self.delay)
        return r

    def _code(self,symbol):
        symbol=str(symbol).upper()
        if symbol in self.codes:return self.codes[symbol]
        try:
            payload=self._get(SEARCH,{"Type":"SS","text":symbol}).json()
            rows=payload if isinstance(payload,list) else payload.get("Table",payload.get("data",[]))
            options=[]
            for row in rows if isinstance(rows,list) else []:
                code=row.get("SCRIP_CD") or row.get("ScripCode") or row.get("scripcode")
                ticker=row.get("scrip_id") or row.get("SCRIP_ID") or row.get("symbol") or ""
                name=row.get("SCRIP_NAME") or row.get("SecurityName") or row.get("NAME") or ""
                if code: options.append((str(code),str(ticker).upper(),str(name).upper()))
            exact=next((c for c,t,n in options if t==symbol),None)
            if exact is None: exact=next((c for c,t,n in options if symbol==n.replace(" ","")),None)
            if exact is None and len(options)==1: exact=options[0][0]
            self.codes[symbol]=exact
            return exact
        except Exception:
            self.codes[symbol]=None
            return None

    def _catalog(self,symbol):
        symbol=str(symbol).upper()
        if symbol in self.catalog_cache:return self.catalog_cache[symbol]
        code=self._code(symbol)
        if not code:
            self.catalog_cache[symbol]=[]
            return []
        rows=[]
        for page in range(1,self.max_pages+1):
            try:
                payload=self._get(ANN,{"pageno":page,"strCat":"Result","subcategory":"Financial+Results",
                    "strSearch":"P","strType":"C","strScrip":code,"strPrevDate":"20190101","strToDate":"20261231"}).json()
            except Exception:
                break
            page_rows=payload.get("Table",[]) if isinstance(payload,dict) else []
            if not page_rows:break
            for item in page_rows:
                headline=" ".join(str(item.get(k,"")) for k in ("NEWSSUB","HEADLINE","CATEGORYNAME"))
                if "result" not in headline.lower() and "financial" not in headline.lower():continue
                news=item.get("NEWSID") or item.get("NewsId")
                sc=item.get("SCRIP_CD") or item.get("ScripCode") or code
                available=_dt(item.get("DissemDT") or item.get("DT_TM") or item.get("News_submission_dt"))
                if not news or not available:continue
                url=f"https://www.bseindia.com/Msource/90D/CorpXbrlGen.aspx?Bsenewid={news}&Scripcode={sc}"
                rows.append({"symbol":symbol,"scripcode":str(sc),"news_id":str(news),
                  "available_at":available.isoformat(),"xbrl_url":url,"headline":headline})
            if len(page_rows)<50:break
        ded={(x["xbrl_url"],x["available_at"]):x for x in rows}
        self.catalog_cache[symbol]=sorted(ded.values(),key=lambda x:x["available_at"])
        return self.catalog_cache[symbol]

    def _parse(self,filing):
        url=filing["xbrl_url"]
        if url in self.doc_cache:return self.doc_cache[url]
        try:
            soup=BeautifulSoup(self._get(url).content,"html.parser")
            contexts={}
            for el in soup.find_all(["xbrli:context","context"]):
                cid=el.get("id")
                if not cid:continue
                inst=el.find(["xbrli:instant","instant"])
                start=el.find(["xbrli:startdate","startdate"])
                end=el.find(["xbrli:enddate","enddate"])
                contexts[cid]={"instant":_dt(inst.get_text(strip=True)) if inst else None,
                  "start":_dt(start.get_text(strip=True)) if start else None,
                  "end":_dt(end.get_text(strip=True)) if end else None}
            facts=[]
            for el in soup.find_all(["ix:nonfraction","nonfraction","ix:nonnumeric","nonnumeric"]):
                metric=_metric(el.get("name") or "")
                if not metric:continue
                ctx=contexts.get(el.get("contextref"),{})
                end=ctx.get("end") or ctx.get("instant")
                if not end:continue
                raw=el.get_text(" ",strip=True).replace(",","").replace("₹","").strip()
                try:value=float(raw)*(10**int(el.get("scale") or 0))
                except Exception:continue
                if str(el.get("sign","")).strip()=="-":value=-value
                facts.append({"symbol":filing["symbol"],"metric":metric,"value":value,
                  "period_end":end.date().isoformat(),
                  "period_start":ctx["start"].date().isoformat() if ctx.get("start") else None,
                  "available_at":filing["available_at"],"basis":"BSE_XBRL",
                  "source_type":"BSE_OFFICIAL_ARCHIVE_XBRL","source_url":url,
                  "source_exchange":"BSE","source_news_id":filing["news_id"]})
            self.doc_cache[url]=facts
            return facts
        except Exception:
            return []

    def facts(self,symbol,as_of):
        cutoff=_dt(str(as_of)+" 23:59:59")
        if not cutoff:return []
        out=[]
        for filing in self._catalog(symbol):
            available=_dt(filing["available_at"])
            if not available or available>cutoff:continue
            for fact in self._parse(filing):
                pe=_dt(fact.get("period_end"))
                av=_dt(fact.get("available_at"))
                if pe and av and pe<=cutoff and av<=cutoff:out.append(fact)
        return out
