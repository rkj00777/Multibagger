import pandas as pd, duckdb, numpy as np
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

def _bootstrap_mean_ci(values, n_boot=10000, seed=42):
    x=pd.Series(values).dropna().astype(float).to_numpy()
    if len(x)==0: return {"n":0,"mean":None,"ci_low":None,"ci_high":None}
    rng=np.random.default_rng(seed)
    means=rng.choice(x,(n_boot,len(x))).mean(axis=1)
    return {"n":int(len(x)),"mean":float(x.mean()),"ci_low":float(np.quantile(means,.025)),"ci_high":float(np.quantile(means,.975))}

def _spearman_permutation_p(x,y,n_perm=10000,seed=42):
    a=pd.to_numeric(pd.Series(x),errors="coerce")
    b=pd.to_numeric(pd.Series(y),errors="coerce")
    m=a.notna()&b.notna(); a=a[m].rank(method="average").to_numpy(float); b=b[m].rank(method="average").to_numpy(float)
    n=len(a)
    if n<8:return {"n":int(n),"rho":None,"p_value":None}
    ac=a-a.mean(); bc=b-b.mean(); denom=np.sqrt((ac*ac).sum()*(bc*bc).sum())
    rho=float((ac*bc).sum()/denom) if denom else 0.0
    rng=np.random.default_rng(seed); ge=1
    for _ in range(n_perm):
        bp=rng.permutation(b); pc=float((ac*(bp-bp.mean())).sum()/denom) if denom else 0.0
        if pc>=rho: ge+=1
    return {"n":int(n),"rho":rho,"p_value":float(ge/(n_perm+1))}

def _bh_fdr(pvals):
    vals=[float(p) if p is not None and np.isfinite(p) else np.nan for p in pvals]
    finite=sorted([(i,p) for i,p in enumerate(vals) if np.isfinite(p)],key=lambda z:z[1])
    q=[None]*len(vals); m=len(finite); prev=1.0
    for rank,(i,p) in reversed(list(enumerate(finite,1))):
        prev=min(prev,p*m/rank); q[i]=float(prev)
    return q

def _statistical_validation(out):
    modules=["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]
    tests=[]
    for mod in modules:
        t=_spearman_permutation_p(out[mod],out["forward_return"],n_perm=10000,seed=42); t["module"]=mod; tests.append(t)
    for t,q in zip(tests,_bh_fdr([x["p_value"] for x in tests])):
        t["q_value"]=q; t["fdr_significant"]=bool(q is not None and q<=.05)
    cut=out.fundamental_score.quantile(.75); top=out[out.fundamental_score>=cut].forward_return.dropna(); allr=out.forward_return.dropna()
    lift=None
    if len(top) and len(allr) and abs(allr.mean())>1e-12:
        rng=np.random.default_rng(103); vals=[]
        for _ in range(10000):
            tm=rng.choice(top.to_numpy(),len(top)).mean(); am=rng.choice(allr.to_numpy(),len(allr)).mean()
            if abs(am)>1e-12: vals.append(tm/am)
        if vals: lift={"mean":float(top.mean()/allr.mean()),"ci_low":float(np.quantile(vals,.025)),"ci_high":float(np.quantile(vals,.975))}
    return {"bootstrap_iterations":10000,"top_quartile_mean_ci":_bootstrap_mean_ci(top,seed=101),"all_observations_mean_ci":_bootstrap_mean_ci(allr,seed=102),"selection_lift_ci":lift,"module_tests":tests,"fdr_method":"Benjamini-Hochberg","fdr_alpha":.05,"statistical_gate":"PASS" if any(t["fdr_significant"] for t in tests) else "NOT_PASSED"}

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
    return {"status":"PIT_MODULE_VALIDATION_COMPLETE","observations":int(len(out)),"decision_dates":int(out.decision_date.nunique()),"top_quartile_forward_return":float(top.mean()) if len(top) else None,"all_forward_return":float(allr.mean()) if len(allr) else None,"selection_lift":float(top.mean()/allr.mean()) if len(top) and len(allr) and allr.mean()!=0 else None,"positive_hit_rate":float((top>0).mean()) if len(top) else None,"next_step":"statistical validation completed; confidence intervals and BH-FDR are now reported before promotion gate", "pit_source":"NSE PIT store plus dynamic NSE PIT fallback", "statistical_validation":_statistical_validation(out)}
