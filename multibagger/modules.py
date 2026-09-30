import pandas as pd
MODULES=["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]

def _growth(cur,prev):
    try:
        if pd.notna(cur) and pd.notna(prev) and float(prev)!=0:return float(cur)/abs(float(prev))-1.0
    except Exception:pass
    return float("nan")

def score_fundamentals(price_df,facts):
    if not facts:return pd.DataFrame()
    f=pd.DataFrame(facts);f["period_end"]=pd.to_datetime(f.period_end,errors="coerce");f["available_at"]=pd.to_datetime(f.available_at,errors="coerce")
    p=f.pivot_table(index=["symbol","period_end"],columns="metric",values="value",aggfunc="last").reset_index()
    rows=[]
    for sym,g in p.groupby("symbol"):
        g=g.sort_values("period_end");cur=g.iloc[-1];prev=g.iloc[-2] if len(g)>1 else None
        pat=float(cur.get("pat",float("nan")));cfo=float(cur.get("cfo",float("nan")));debt=float(cur.get("debt",float("nan")));cash=float(cur.get("cash",float("nan")));equity=float(cur.get("equity",float("nan")));eps=float(cur.get("eps",float("nan")))
        revenue=float(cur.get("revenue",float("nan")));prev_revenue=float(prev.get("revenue",float("nan"))) if prev is not None else float("nan")
        prev_pat=float(prev.get("pat",float("nan"))) if prev is not None else float("nan")
        ebitda=float(cur.get("ebitda",float("nan")));prev_ebitda=float(prev.get("ebitda",float("nan"))) if prev is not None else float("nan")
        px=price_df.loc[price_df.symbol.eq(sym),"close"];price=float(px.iloc[0]) if len(px) else float("nan")
        pe=price/eps if pd.notna(price) and pd.notna(eps) and eps>0 else float("nan")
        val=100 if pd.notna(pe) and pe<=15 else 80 if pd.notna(pe) and pe<=25 else 60 if pd.notna(pe) and pe<=40 else 40 if pd.notna(pe) and pe<=60 else 20 if pd.notna(pe) else float("nan")
        earn=50
        if prev is not None and pd.notna(prev_pat) and abs(prev_pat)>0: earn=max(0,min(100,50+50*(pat/abs(prev_pat)-1)))
        cashs=max(0,min(100,50+50*(cfo/abs(pat)-1))) if pd.notna(cfo) and pd.notna(pat) and pat!=0 else float("nan")
        roic=max(0,min(100,100*pat/equity)) if pd.notna(pat) and pd.notna(equity) and equity>0 else float("nan")
        lev=(debt-cash)/equity if all(pd.notna(x) for x in [debt,cash,equity]) and equity>0 else float("nan")
        gov=max(0,min(100,80-20*max(lev,0))) if pd.notna(lev) else 25
        module_values={"valuation_gap":val,"earnings_acceleration":earn,"cash_conversion":cashs,"reinvestment_roic":roic,"governance_balance_sheet":gov}
        available=sum(pd.notna(v) for v in module_values.values())
        evidence=min(1.0,len(g)/4.0)*(available/5.0)
        rg=_growth(revenue,prev_revenue); pg=_growth(pat,prev_pat); eg=_growth(ebitda,prev_ebitda)
        cc=(cfo/revenue)-(float(prev.get("cfo",float("nan")))/prev_revenue) if prev is not None and pd.notna(cfo) and pd.notna(revenue) and pd.notna(prev.get("cfo")) and pd.notna(prev_revenue) and prev_revenue!=0 and revenue!=0 else float("nan")
        rows.append({"symbol":sym,**module_values,"fundamental_evidence":evidence,"module_coverage":available/5.0,"pe_proxy":pe,
                     "revenue_growth":rg,"pat_growth":pg,"ebitda_growth":eg,
                     "operating_leverage":pg-rg if pd.notna(pg) and pd.notna(rg) else float("nan"),
                     "cash_conversion_change":cc})
    return pd.DataFrame(rows)
