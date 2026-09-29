import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pandas as pd
import requests

BASE="https://www.screener.in/company/"

def _num(v):
    if v is None: return None
    s=str(v).replace(",","").replace("%","").strip()
    if s in ("","-","nan","None"): return None
    try: return float(s)
    except: return None

def _period(v):
    s=str(v).strip()
    if re.fullmatch(r"Mar \d{4}",s): return datetime.strptime(s,"%b %Y").date().isoformat()
    return None

def _flatten(df):
    x=df.copy()
    if isinstance(x.columns,pd.MultiIndex):
        cols=[]
        for c in x.columns:
            vals=[str(v) for v in c if str(v)!="nan"]
            cols.append(vals[-1] if vals else "")
        x.columns=cols
    else: x.columns=[str(c) for c in x.columns]
    x.iloc[:,0]=x.iloc[:,0].astype(str).str.replace(r"[+]$","",regex=True).str.strip()
    return x

def _find_table(tables, labels):
    labels={x.lower() for x in labels}
    for t in tables:
        if t.empty: continue
        x=_flatten(t)
        first=x.iloc[:,0].astype(str).str.lower().tolist()
        if any(a in first for a in labels): return x
    return None

def _row(table, label):
    if table is None: return None
    q=table[table.iloc[:,0].astype(str).str.lower().eq(label.lower())]
    if q.empty: return None
    return q.iloc[0]

def _annual_facts(symbol, html, as_of):
    try: tables=pd.read_html(html)
    except: return []
    pl=_find_table(tables,{"sales","net profit","eps in rs"})
    bs=_find_table(tables,{"borrowings","equity capital","reserves"})
    cf=_find_table(tables,{"cash from operating activity","free cash flow"})
    if pl is None: return []
    labels={"sales":"Sales","pat":"Net Profit","eps":"EPS in Rs","debt":"Borrowings","equity_cap":"Equity Capital","reserves":"Reserves","cfo":"Cash from Operating Activity","fcf":"Free Cash Flow"}
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
    for c,p in periods[-6:]:
        vals={k:_num(r.get(c)) if r is not None else None for k,r in rows.items()}
        if vals.get("pat") is None and vals.get("sales") is None: continue
        for metric,key in (("revenue","sales"),("pat","pat"),("eps","eps"),("debt","debt")):
            if vals.get(key) is not None: out.append({"metric":metric,"value":vals[key],"period_end":p,"available_at":as_of,"source_type":"SCREENER_PUBLIC_FALLBACK","source_url":BASE+symbol+"/","symbol":symbol})
        equity=(vals.get("equity_cap") or 0)+(vals.get("reserves") or 0)
        if equity>0: out.append({"metric":"equity","value":equity,"period_end":p,"available_at":as_of,"source_type":"SCREENER_PUBLIC_FALLBACK","source_url":BASE+symbol+"/","symbol":symbol})
        for metric,key in (("cfo","cfo"),("fcf","fcf")):
            if vals.get(key) is not None: out.append({"metric":metric,"value":vals[key],"period_end":p,"available_at":as_of,"source_type":"SCREENER_PUBLIC_FALLBACK","source_url":BASE+symbol+"/","symbol":symbol})
    return out

class ScreenerFundamentals:
    """Free fallback for public Screener annual P&L, balance-sheet and cash-flow data.
    Deliberately labelled non-PIT because page publication timestamps are not independently recoverable.
    """
    def __init__(self,workers=12):
        self.workers=workers
        self.s=requests.Session()
        self.s.headers.update({"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36","Accept":"text/html,application/xhtml+xml"})
    def one(self,symbol,as_of):
        try:
            u=BASE+symbol+"/consolidated/"
            r=self.s.get(u,timeout=10)
            if not r.ok or len(r.text)<5000:
                u=BASE+symbol+"/"
                r=self.s.get(u,timeout=25)
            if r.ok: return _annual_facts(symbol,r.text,as_of)
        except: pass
        return []
    def batch(self,symbols,as_of):
        out=[]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            fut={ex.submit(self.one,s,as_of):s for s in symbols}
            for f in as_completed(fut):
                try: out.extend(f.result())
                except: pass
        return out
