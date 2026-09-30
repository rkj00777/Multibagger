import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pandas as pd
import requests

BASE="https://www.screener.in/company/"
JINA_BASE="https://r.jina.ai/http://www.screener.in/company/"

def _num(v):
    if v is None: return None
    s=str(v).replace("\xa0"," ").replace(",","").replace("%","").strip()
    if s in ("","-","nan","None"): return None
    try: return float(s)
    except: return None

def _period(v):
    s=str(v).replace("\xa0"," ").strip()
    if s.upper()=="TTM": return None
    m=re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})",s,re.I)
    if not m: return None
    try: return datetime.strptime(f"{m.group(1)[:3].title()} {m.group(2)}","%b %Y").date().isoformat()
    except: return None

def _flatten(df):
    x=df.copy()
    if x.empty: return x
    if isinstance(x.columns,pd.MultiIndex):
        cols=[]
        for c in x.columns:
            vals=[str(v).replace("\xa0"," ").strip() for v in c if str(v)!="nan"]
            cols.append(vals[-1] if vals else "")
        x.columns=cols
    else:
        x.columns=[str(c).replace("\xa0"," ").strip() for c in x.columns]
    for i in range(min(3,x.shape[1])):
        x.iloc[:,i]=x.iloc[:,i].astype(str).str.replace("\xa0"," ",regex=False).str.replace(r"[+]$","",regex=True).str.strip()
    return x

def _norm_label(v):
    s=str(v).replace("\xa0"," ").lower()
    return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9 ]+"," ",s)).strip()

def _label_match(v, labels):
    a=_norm_label(v)
    return any(a==b or a.startswith(b) or b.startswith(a) for b in labels)

def _find_table(tables, labels):
    labels={_norm_label(x) for x in labels}
    best=None
    best_hits=0
    for t in tables:
        if t.empty: continue
        x=_flatten(t)
        hits=0
        for i in range(min(3,x.shape[1])):
            hits += sum(_label_match(v,labels) for v in x.iloc[:,i].tolist())
        if hits>best_hits:
            best=x; best_hits=hits
    return best if best_hits else None

def _row(table, label):
    if table is None or table.empty: return None
    target=_norm_label(label)
    for i in range(min(3,table.shape[1])):
        col=table.iloc[:,i].map(_norm_label)
        mask=col.map(lambda x: x==target or x.startswith(target) or target.startswith(x))
        q=table[mask]
        if not q.empty: return q.iloc[0]
    return None

def _annual_facts(symbol, html, as_of, source_type, source_url):
    try: tables=pd.read_html(html)
    except Exception: return []
    pl=_find_table(tables,{"sales","net profit","eps in rs"})
    bs=_find_table(tables,{"borrowings","equity capital","reserves"})
    cf=_find_table(tables,{"cash from operating activity","free cash flow"})
    if pl is None: return []
    labels={"sales":"Sales","pat":"Net Profit","eps":"EPS in Rs","debt":"Borrowings",
            "equity_cap":"Equity Capital","reserves":"Reserves",
            "cfo":"Cash from Operating Activity","fcf":"Free Cash Flow"}
    rows={}
    for k,label in labels.items():
        src=pl if k in ("sales","pat","eps") else bs if k in ("debt","equity_cap","reserves") else cf
        r=_row(src,label)
        if r is not None: rows[k]=r
    periods=[]
    for c in pl.columns[1:]:
        p=_period(c)
        if p: periods.append((c,p))
    out=[]
    for c,p in periods[-10:]:
        vals={k:_num(r.get(c)) if r is not None and c in r.index else None for k,r in rows.items()}
        if vals.get("pat") is None and vals.get("sales") is None: continue
        for metric,key in (("revenue","sales"),("pat","pat"),("eps","eps"),("debt","debt")):
            if vals.get(key) is not None:
                out.append({"metric":metric,"value":vals[key],"period_end":p,"available_at":as_of,
                            "source_type":source_type,"source_url":source_url,"symbol":symbol})
        equity=(vals.get("equity_cap") or 0)+(vals.get("reserves") or 0)
        if equity>0:
            out.append({"metric":"equity","value":equity,"period_end":p,"available_at":as_of,
                        "source_type":source_type,"source_url":source_url,"symbol":symbol})
        for metric,key in (("cfo","cfo"),("fcf","fcf")):
            if vals.get(key) is not None:
                out.append({"metric":metric,"value":vals[key],"period_end":p,"available_at":as_of,
                            "source_type":source_type,"source_url":source_url,"symbol":symbol})
    return out

class ScreenerFundamentals:
    """Online public-data fallback. Current pages contain historical periods; publication timestamps are not treated as PIT."""
    def __init__(self,workers=12):
        self.workers=workers
        self.s=requests.Session()
        self.s.headers.update({
            "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            "Accept":"text/html,application/xhtml+xml,application/xhtml+xml"
        })

    def one(self,symbol,as_of):
        urls=[
            (BASE+symbol+"/consolidated/","SCREENER_PUBLIC_FALLBACK"),
            (BASE+symbol+"/","SCREENER_PUBLIC_FALLBACK"),
            (JINA_BASE+symbol+"/consolidated/","SCREENER_JINA_PUBLIC_FALLBACK"),
            (JINA_BASE+symbol+"/","SCREENER_JINA_PUBLIC_FALLBACK"),
        ]
        for u,stype in urls:
            try:
                r=self.s.get(u,timeout=25)
                if r.ok and len(r.text)>=5000:
                    facts=_annual_facts(symbol,r.text,as_of,stype,u)
                    if facts: return facts
            except Exception:
                continue
        return []

    def batch(self,symbols,as_of):
        out=[]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            fut={ex.submit(self.one,s,as_of):s for s in symbols}
            for f in as_completed(fut):
                try: out.extend(f.result())
                except Exception: pass
        return out
