import pandas as pd, duckdb
from core.market_data import nse_cross_section
from core.nse_pit import NSEPIT
from core.pit_store import load_facts
from multibagger.modules import score_fundamentals
HF="https://huggingface.co/datasets/tejhq/indian-markets/resolve/main"
def forward_returns(symbols,as_of,horizon_days=252):
    y=int(as_of[:4]); urls=[f"{HF}/nse/year={z}/nse_{z}.parquet" for z in range(y,min(y+2,2027))]
    c=duckdb.connect(); paths="["+",".join(repr(x) for x in urls)+"]"
    syms=",".join("'"+s.replace("'","''")+"'" for s in symbols)
    q=f"""WITH p AS (SELECT symbol,date,close FROM read_parquet({paths},union_by_name=true) WHERE symbol IN ({syms})),
    a AS (SELECT symbol,arg_max(close,date) FILTER(WHERE date<=DATE '{as_of}') px FROM p GROUP BY symbol),
    b AS (SELECT symbol,arg_min(close,date) FILTER(WHERE date>=DATE '{as_of}'+INTERVAL '{horizon_days} days') fx FROM p GROUP BY symbol)
    SELECT a.symbol,a.px,b.fx,b.fx/a.px-1 forward_return FROM a LEFT JOIN b USING(symbol)"""
    out=c.execute(q).fetchdf();c.close();return out
def validate(months,top_n=25):
    rows=[]
    for as_of in months:
        px=nse_cross_section(as_of)
        if px.empty:continue
        px["technical"]=px.ret_63d.rank(pct=True)*25+px.ret_126d.rank(pct=True)*35+px.ret_252d.rank(pct=True)*40
        pool=px[(px.avg_turnover_60d>=2_000_000)&px.ret_126d.notna()].nlargest(top_n,"technical")
        facts_df=load_facts(as_of)
        facts=facts_df[facts_df.symbol.isin(pool.symbol)] if not facts_df.empty else pd.DataFrame()
        pit_symbols=set(facts.symbol.astype(str).unique()) if not facts.empty else set()
        missing=[s for s in pool.symbol.astype(str).tolist() if s not in pit_symbols]
        if missing:
            try:
                dyn=NSEPIT(); rows_dyn=[]
                for sym in missing:
                    try: rows_dyn.extend(dyn.facts(sym,as_of) or [])
                    except Exception: pass
                if rows_dyn:
                    ddf=pd.DataFrame(rows_dyn)
                    facts=pd.concat([facts,ddf],ignore_index=True) if not facts.empty else ddf
            except Exception:
                pass
        fs=score_fundamentals(pool,facts.to_dict(orient="records") if isinstance(facts,pd.DataFrame) else facts)
        if fs.empty:continue
        fr=forward_returns(fs.symbol.tolist(),as_of)
        z=fs.merge(fr,on="symbol",how="inner");z["fundamental_score"]=z[["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]].mean(axis=1,skipna=True)
        z["fundamental_module_count"]=z[["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]].notna().sum(axis=1)
        z["decision_date"]=as_of;rows.extend(z[["decision_date","symbol","fundamental_score","fundamental_module_count","fundamental_evidence","forward_return"]].to_dict("records"))
    out=pd.DataFrame(rows)
    if out.empty:return {"status":"NO_VALIDATION_OBSERVATIONS"}
    q=out.fundamental_score.quantile(.75);top=out[out.fundamental_score>=q].forward_return.dropna();allr=out.forward_return.dropna()
    return {"status":"PIT_MODULE_VALIDATION_COMPLETE","observations":int(len(out)),"decision_dates":int(out.decision_date.nunique()),"top_quartile_forward_return":float(top.mean()) if len(top) else None,"all_forward_return":float(allr.mean()) if len(allr) else None,"selection_lift":float(top.mean()/allr.mean()) if len(top) and len(allr) and allr.mean()!=0 else None,"positive_hit_rate":float((top>0).mean()) if len(top) else None,"next_step":"bootstrap confidence interval + Benjamini-Hochberg across individual modules before promotion gate", "pit_source":"NSE PIT store plus dynamic NSE PIT fallback"}
