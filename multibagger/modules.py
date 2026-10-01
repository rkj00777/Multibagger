import pandas as pd

MODULES=["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]

def _growth(cur,prev):
    try:
        if pd.notna(cur) and pd.notna(prev) and float(prev)!=0:
            return float(cur)/abs(float(prev))-1.0
    except Exception:
        pass
    return float("nan")

def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return float("nan")

def score_fundamentals(price_df,facts):
    """Score available PIT fundamentals without requiring a perfectly complete XBRL filing.

    The old scorer made a stock fail before MBE whenever one of the five modules or
    four historical periods was missing.  This version is evidence-weighted:
    available modules contribute to the score, while evidence measures both breadth
    and longitudinal depth.  Missing data never becomes a fabricated zero.
    """
    if not facts:
        return pd.DataFrame()

    f=pd.DataFrame(facts).copy()
    if f.empty:
        return pd.DataFrame()
    f["period_end"]=pd.to_datetime(f.get("period_end"),errors="coerce")
    f["available_at"]=pd.to_datetime(f.get("available_at"),errors="coerce")
    f=f[f["period_end"].notna()].copy()

    p=f.pivot_table(index=["symbol","period_end"],columns="metric",values="value",aggfunc="last").reset_index()
    rows=[]

    for sym,g in p.groupby("symbol"):
        g=g.sort_values("period_end")
        cur=g.iloc[-1]
        prev=g.iloc[-2] if len(g)>1 else None

        pat=_safe_float(cur.get("pat"))
        cfo=_safe_float(cur.get("cfo"))
        debt=_safe_float(cur.get("debt"))
        cash=_safe_float(cur.get("cash"))
        equity=_safe_float(cur.get("equity"))
        eps=_safe_float(cur.get("eps"))
        revenue=_safe_float(cur.get("revenue"))
        prev_revenue=_safe_float(prev.get("revenue")) if prev is not None else float("nan")
        prev_pat=_safe_float(prev.get("pat")) if prev is not None else float("nan")
        ebitda=_safe_float(cur.get("ebitda"))
        prev_ebitda=_safe_float(prev.get("ebitda")) if prev is not None else float("nan")

        px=price_df.loc[price_df.symbol.eq(sym),"close"]
        price=_safe_float(px.iloc[0]) if len(px) else float("nan")
        pe=price/eps if pd.notna(price) and pd.notna(eps) and eps>0 else float("nan")

        # Valuation is intentionally coarse: it is a screening module, not a target-price model.
        val=(100 if pe<=15 else 80 if pe<=25 else 60 if pe<=40 else 40 if pe<=60 else 20) if pd.notna(pe) else float("nan")

        earn=50.0
        if pd.notna(prev_pat) and abs(prev_pat)>0 and pd.notna(pat):
            earn=max(0,min(100,50+50*(pat/abs(prev_pat)-1)))

        cashs=float("nan")
        if pd.notna(cfo) and pd.notna(pat) and pat!=0:
            cashs=max(0,min(100,50+50*(cfo/abs(pat)-1)))

        roic=float("nan")
        if pd.notna(pat) and pd.notna(equity) and equity>0:
            roic=max(0,min(100,100*pat/equity))

        gov=25.0
        if all(pd.notna(x) for x in [debt,cash,equity]) and equity>0:
            lev=(debt-cash)/equity
            gov=max(0,min(100,80-20*max(lev,0)))

        module_values={
            "valuation_gap":val,
            "earnings_acceleration":earn,
            "cash_conversion":cashs,
            "reinvestment_roic":roic,
            "governance_balance_sheet":gov,
        }

        available=sum(pd.notna(v) for v in module_values.values())
        module_coverage=available/len(MODULES)

        # Longitudinal depth is capped at four distinct reporting periods.
        periods=int(g["period_end"].nunique())
        period_coverage=min(1.0,periods/4.0)

        # Evidence is a quality/breadth measure, not a hard data-availability gate.
        # A stock with 3/5 modules over 2 periods can enter MBE; promotion still
        # requires the stronger evidence threshold in engine.py.
        evidence=0.60*module_coverage+0.40*period_coverage

        rg=_growth(revenue,prev_revenue)
        pg=_growth(pat,prev_pat)
        eg=_growth(ebitda,prev_ebitda)

        cc=float("nan")
        if prev is not None and pd.notna(cfo) and pd.notna(revenue) and pd.notna(prev.get("cfo")) and pd.notna(prev_revenue) and prev_revenue!=0 and revenue!=0:
            cc=(cfo/revenue)-(_safe_float(prev.get("cfo"))/prev_revenue)

        rows.append({
            "symbol":sym,
            **module_values,
            "fundamental_evidence":evidence,
            "module_coverage":module_coverage,
            "period_coverage":period_coverage,
            "fundamental_periods":periods,
            "pe_proxy":pe,
            "revenue_growth":rg,
            "pat_growth":pg,
            "ebitda_growth":eg,
            "operating_leverage":pg-rg if pd.notna(pg) and pd.notna(rg) else float("nan"),
            "cash_conversion_change":cc,
        })

    return pd.DataFrame(rows)
