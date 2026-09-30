import pandas as pd
from core.market_data import nse_cross_section
from core.pit_store import load_facts, coverage as pit_coverage
from core.catalyst import NSECatalyst, catalyst_score
from multibagger.modules import score_fundamentals
from multibagger.early_inflection import score_early_inflection
from multibagger.firewall import apply_trap_firewall

VERSION="4.1.0-exchange-pit"

def _pct(s): return s.rank(pct=True)*100

def run(as_of:str):
    df=nse_cross_section(as_of)
    if df.empty: return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"status":"DATA_UNAVAILABLE"}
    for c in ["ret_63d","ret_126d","ret_252d","avg_turnover_60d","pct_off_high"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    pct=lambda s:s.rank(pct=True)*100
    df["trend_score"]=pct(df.ret_63d.fillna(-1e9))*.25+pct(df.ret_126d.fillna(-1e9))*.35+pct(df.ret_252d.fillna(-1e9))*.40
    df["liquidity_score"]=pct(df.avg_turnover_60d.fillna(0)); df["discovery_score"]=df.trend_score*.75+df.liquidity_score*.25
    eligible=df[(df.avg_turnover_60d>=2_000_000)&df.ret_126d.notna()].copy()
    discovery=eligible.sort_values(["discovery_score","liquidity_score"],ascending=False).head(150).copy()
    pit=load_facts(as_of); pit_stats=pit_coverage(pit)
    facts=pit[pit.symbol.isin(discovery.symbol)] if not pit.empty else pd.DataFrame()
    records=facts.to_dict(orient="records") if not facts.empty else []
    if not records:
        return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"status":"DATA_LAYER_INCOMPLETE","universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),"discovery_pool":int(len(discovery)),"fundamental_pool":0,"mbe_pool":0,"final_shortlist":0,"fundamental_facts":0,"fundamental_data_status":"NSE_XBRL_PIT_STORE_EMPTY","pit_coverage":pit_stats,"promotion_count":0,"promotion_block":"No production promotion until exchange-timestamped PIT fundamentals are available","validation_status":"CURRENT_PIT_LIVE_MODULE_RUN; HISTORICAL_WINNER_AUDIT_SEPARATE"}
    fs=score_fundamentals(discovery,records); discovery=discovery.merge(fs,on="symbol",how="left")
    discovery["fundamental_score"]=discovery[["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]].mean(axis=1,skipna=False)
    # 150 -> 30: PIT fundamentals only, no catalyst.
    fundamental_pool=discovery[discovery.fundamental_score.notna()&discovery.fundamental_evidence.ge(.50)].sort_values(["fundamental_score","fundamental_evidence","discovery_score"],ascending=False).head(30).copy()
    if fundamental_pool.empty:
        return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"status":"LIVE_SCAN_COMPLETE","universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),"discovery_pool":int(len(discovery)),"fundamental_pool":0,"mbe_pool":0,"final_shortlist":0,"fundamental_facts":int(len(records)),"fundamental_data_status":"NSE_XBRL_PIT_STORE","pit_coverage":pit_stats,"promotion_count":0,"promotion_block":"No candidates cleared PIT fundamental confirmation","validation_status":"CURRENT_PIT_LIVE_MODULE_RUN; HISTORICAL_WINNER_AUDIT_SEPARATE"}
    # 30 -> 15: catalyst-free MBE core.
    ei=score_early_inflection(fundamental_pool,fundamental_pool,{})
    for c in ["earnings_inflection","operating_leverage","cash_inflection","balance_sheet_runway"]:
        if c in ei: fundamental_pool[c]=ei[c].values
    from multibagger.mbe_core import score_mbe_core
    fundamental_pool=score_mbe_core(fundamental_pool)
    pre=apply_trap_firewall(fundamental_pool); fundamental_pool["trap_flags"]=pre["trap_flags"]; fundamental_pool["trap_firewall_pass"]=pre["trap_firewall_pass"]
    mbe_pool=fundamental_pool.sort_values(["trap_firewall_pass","mbe_score"],ascending=False).head(15).copy()
    # 15 -> 8: catalyst/news verification only on MBE finalists.
    cats=NSECatalyst(workers=4).batch(mbe_pool.symbol.tolist(),as_of); catmap={}
    for item in cats:
        sym=item.get("symbol") or item.get("sym")
        if sym: catmap.setdefault(sym,[]).append(item)
    mbe_pool["catalyst_score"]=mbe_pool.symbol.map(lambda s:catalyst_score(catmap.get(s,[])))
    ei_final=score_early_inflection(mbe_pool,mbe_pool,catmap)
    for c in ["order_visibility","capacity_inflection","structural_theme","early_inflection_score","early_stage"]:
        if c in ei_final: mbe_pool[c]=ei_final[c].values
    final=mbe_pool[mbe_pool.trap_firewall_pass&mbe_pool.catalyst_score.ge(55)].sort_values(["catalyst_score","mbe_score","fundamental_score"],ascending=False).head(8).copy()
    promoted=final[final.mbe_score.ge(65)&final.fundamental_score.ge(60)&final.fundamental_evidence.ge(.75)&final.catalyst_score.ge(60)&final.trap_firewall_pass].copy()
    cols=[c for c in ["symbol","name","close","ret_63d","ret_126d","ret_252d","discovery_score","fundamental_score","fundamental_evidence","mbe_score","catalyst_score","earnings_inflection","order_visibility","capacity_inflection","structural_theme","early_inflection_score","early_stage","trap_flags","trap_firewall_pass","promoted"] if c in final.columns]
    return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"data_date":str(df.data_date.max())[:10],"status":"LIVE_SCAN_COMPLETE","universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),"discovery_pool":int(len(discovery)),"fundamental_pool":int(len(fundamental_pool)),"mbe_pool":int(len(mbe_pool)),"final_shortlist":int(len(final)),"fundamental_facts":int(len(records)),"fundamental_data_status":"NSE_XBRL_PIT_STORE","pit_rule":"Only facts with NSE exchange availability/broadcast timestamp <= as_of are eligible","pit_coverage":pit_stats,"catalyst_data_status":"NSE_CORPORATE_ANNOUNCEMENTS_FINALIST_ONLY","promotion_count":int(len(promoted)),"promotion_block":None if len(promoted) else "No finalist cleared all MBE, catalyst, evidence and firewall gates","funnel":["5000+ universe","150 discovery","30 fundamental","15 MBE","8 catalyst finalists"],"final_candidates":final[cols].to_dict(orient="records"),"promoted_candidates":promoted[cols].to_dict(orient="records"),"validation_status":"CURRENT_PIT_LIVE_MODULE_RUN; HISTORICAL_WINNER_AUDIT_SEPARATE"}
