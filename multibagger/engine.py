import pandas as pd
from core.market_data import nse_cross_section
from core.nse_fundamentals import NSEFundamentals
from core.catalyst import NSECatalyst, catalyst_score
from multibagger.modules import score_fundamentals
from multibagger.firewall import apply_trap_firewall
VERSION="3.0.0-free-pit-aware"
def _pct(s): return s.rank(pct=True)*100
def run(as_of:str):
    df=nse_cross_section(as_of)
    if df.empty:return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"status":"DATA_UNAVAILABLE"}
    for c in ["ret_63d","ret_126d","ret_252d","avg_turnover_60d","pct_off_high"]:df[c]=pd.to_numeric(df[c],errors="coerce")
    df["trend_score"]=_pct(df.ret_63d.fillna(-1e9))*.25+_pct(df.ret_126d.fillna(-1e9))*.35+_pct(df.ret_252d.fillna(-1e9))*.40
    df["liquidity_score"]=_pct(df.avg_turnover_60d.fillna(0));df["discovery_score"]=df.trend_score*.75+df.liquidity_score*.25
    eligible=df[(df.avg_turnover_60d>=2_000_000)&df.ret_126d.notna()].copy()
    discovery=eligible.sort_values(["discovery_score","ret_126d"],ascending=False).head(100).copy()
    fundamentals=NSEFundamentals(workers=8).batch(discovery.symbol.tolist(),as_of)
    fs=score_fundamentals(discovery,fundamentals)
    if not fs.empty: discovery=discovery.merge(fs,on="symbol",how="left")
    else:
        for c in ["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet","fundamental_score","fundamental_evidence"]:discovery[c]=float("nan")
    cats=NSECatalyst(workers=8).batch(discovery.symbol.tolist(),as_of);catmap={}
    for x in cats:
        sym=x.get("symbol") or x.get("sym")
        if sym:catmap.setdefault(sym,[]).append(x)
    discovery["catalyst_score"]=discovery.symbol.map(lambda s:catalyst_score(catmap.get(s,[])))
    discovery["fundamental_score"]=discovery[["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]].mean(axis=1,skipna=False)
    discovery=apply_trap_firewall(discovery)
    discovery["promoted"]=discovery.fundamental_score.ge(65)&discovery.catalyst_score.ge(55)&discovery.fundamental_evidence.ge(.75)&discovery.trap_firewall_pass&discovery.trend_score.ge(70)
    cols=["symbol","name","close","ret_63d","ret_126d","ret_252d","trend_score","discovery_score","fundamental_score","catalyst_score","fundamental_evidence","trap_flags","trap_firewall_pass","promoted"]
    out=discovery[cols].sort_values(["promoted","fundamental_score","discovery_score"],ascending=False).head(30).round(4)
    return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"data_date":str(df.data_date.max())[:10],"status":"LIVE_SCAN_COMPLETE","universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),"candidate_pool":int(len(discovery)),"fundamental_facts":int(len(fundamentals)),"fundamental_data_status":"NSE_INTEGRATED_XBRL_FREE_PUBLIC","pit_rule":"fact.available_at <= as_of; revisions after as_of excluded","fundamental_modules_verified":5,"promotion_count":int(discovery.promoted.sum()),"promotion_block":None if discovery.promoted.any() else "No candidate cleared all five fundamental modules + catalyst + firewall gates","validation_status":"CURRENT_PIT_LIVE_MODULE_RUN; HISTORICAL_WALK_FORWARD_VALIDATION_SEPARATE","top_candidates":out.to_dict(orient="records")}
