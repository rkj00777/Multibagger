import pandas as pd
MODULES=["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet","industry_catalyst"]

def score_fundamentals(price_df,facts):
    if not facts:return pd.DataFrame()
    f=pd.DataFrame(facts)
    f["period_end"]=pd.to_datetime(f.period_end,errors="coerce")
    f["available_at"]=pd.to_datetime(f.available_at,errors="coerce")
    # Only compare facts with the same period; latest two periods are used.
    piv=f.pivot_table(index=["symbol","period_end"],columns="metric",values="value",aggfunc="last").reset_index()
    rows=[]
    for sym,g in piv.groupby("symbol"):
        g=g.sort_values("period_end")
        cur=g.iloc[-1]; prev=g.iloc[-2] if len(g)>1 else None
        revenue=float(cur.get("revenue",float("nan"))); pat=float(cur.get("pat",float("nan")))
        cfo=float(cur.get("cfo",float("nan"))); capex=float(cur.get("capex",float("nan")))
        debt=float(cur.get("debt",float("nan"))); cash=float(cur.get("cash",float("nan")))
        equity=float(cur.get("equity",float("nan")))
        scores={}
        scores["earnings_acceleration"]=50 if prev is None else max(0,min(100,50+50*((pat/(abs(prev.get("pat",pat)) or 1))-1)))
        scores["cash_conversion"]=max(0,min(100,50+50*(cfo/(abs(pat) or 1)-1))) if pd.notna(cfo) and pd.notna(pat) else 25
        scores["reinvestment_roic"]=50 if not (pd.notna(pat) and pd.notna(equity) and equity) else max(0,min(100,100*(pat/equity)))
        leverage=(debt-cash)/(equity or 1) if pd.notna(debt) and pd.notna(cash) and pd.notna(equity) else float("nan")
        scores["governance_balance_sheet"]=max(0,min(100,80-20*max(leverage,0))) if pd.notna(leverage) else 25
        scores["valuation_gap"]=50  # deliberately neutral until price-to-fundamental normalization is added
        rows.append({"symbol":sym,**scores,"fundamental_evidence":min(1.0,len(g)/4)})
    return pd.DataFrame(rows)

def module_status(row):
    return {m:float(row.get(m,0)) for m in MODULES}
