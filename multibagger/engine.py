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
    if df.empty:
        return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"status":"DATA_UNAVAILABLE"}

    for c in ["ret_63d","ret_126d","ret_252d","avg_turnover_60d","pct_off_high"]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df["trend_score"]=_pct(df.ret_63d.fillna(-1e9))*.25+_pct(df.ret_126d.fillna(-1e9))*.35+_pct(df.ret_252d.fillna(-1e9))*.40
    df["liquidity_score"]=_pct(df.avg_turnover_60d.fillna(0))
    df["discovery_score"]=df.trend_score*.75+df.liquidity_score*.25

    eligible=df[(df.avg_turnover_60d>=2_000_000)&df.ret_126d.notna()].copy()
    discovery=eligible.sort_values(["liquidity_score","discovery_score"],ascending=False).head(300).copy()

    # Production fundamental layer: only persisted, timestamped NSE/XBRL evidence.
    pit=load_facts(as_of)
    pit_stats=pit_coverage(pit)
    fundamentals=pit[pit.symbol.isin(discovery.symbol)] if not pit.empty else pd.DataFrame()
    fundamental_records=fundamentals.to_dict(orient="records") if not fundamentals.empty else []

    if not fundamental_records:
        return {
            "engine":"Multibagger","version":VERSION,"as_of_requested":as_of,
            "data_date":str(df.data_date.max())[:10],"status":"DATA_LAYER_INCOMPLETE",
            "universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),
            "candidate_pool":int(len(discovery)),"fundamental_facts":0,
            "fundamental_data_status":"NSE_XBRL_PIT_STORE_EMPTY",
            "pit_coverage":pit_stats,
            "promotion_count":0,
            "promotion_block":"No production promotion until exchange-timestamped PIT fundamentals are available",
            "validation_status":"CURRENT_PIT_LIVE_MODULE_RUN; HISTORICAL_WINNER_AUDIT_SEPARATE",
        }

    fs=score_fundamentals(discovery,fundamental_records)
    if not fs.empty:
        discovery=discovery.merge(fs,on="symbol",how="left")
    else:
        for c in ["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet","fundamental_score","fundamental_evidence","revenue_growth","pat_growth","ebitda_growth","operating_leverage","cash_conversion_change"]:
            discovery[c]=float("nan")

    cats=NSECatalyst(workers=8).batch(discovery.symbol.tolist(),as_of)
    catmap={}
    for x in cats:
        sym=x.get("symbol") or x.get("sym")
        if sym: catmap.setdefault(sym,[]).append(x)
    discovery["catalyst_score"]=discovery.symbol.map(lambda s:catalyst_score(catmap.get(s,[])))
    discovery["fundamental_score"]=discovery[["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]].mean(axis=1,skipna=False)

    ei=score_early_inflection(discovery,discovery,catmap)
    for c in ["earnings_inflection","operating_leverage","cash_inflection","balance_sheet_runway","order_visibility","capacity_inflection","structural_theme","early_inflection_score","early_stage"]:
        if c in ei: discovery[c]=ei[c].values

    discovery=apply_trap_firewall(discovery)
    discovery["early_watch"]=discovery.early_inflection_score.ge(65)&discovery.fundamental_evidence.ge(.50)&discovery.trap_firewall_pass
    discovery["promoted"]=discovery.early_inflection_score.ge(70)&discovery.fundamental_score.ge(65)&discovery.catalyst_score.ge(55)&discovery.fundamental_evidence.ge(.75)&discovery.trap_firewall_pass&discovery.trend_score.ge(45)

    cols=["symbol","name","close","ret_63d","ret_126d","ret_252d","trend_score","discovery_score","fundamental_score","catalyst_score","fundamental_evidence","earnings_inflection","operating_leverage","order_visibility","capacity_inflection","structural_theme","early_inflection_score","early_stage","early_watch","trap_flags","trap_firewall_pass","promoted"]
    out=discovery[cols].sort_values(["promoted","early_watch","early_inflection_score","discovery_score"],ascending=False).head(50).round(4)

    return {
        "engine":"Multibagger","version":VERSION,"as_of_requested":as_of,
        "data_date":str(df.data_date.max())[:10],"status":"LIVE_SCAN_COMPLETE",
        "universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),
        "candidate_pool":int(len(discovery)),"fundamental_facts":int(len(fundamental_records)),
        "fundamental_data_status":"NSE_XBRL_PIT_STORE",
        "pit_rule":"Only facts with NSE exchange availability/broadcast timestamp <= as_of are eligible",
        "pit_coverage":pit_stats,
        "fundamental_modules_verified":5,"early_inflection_modules":9,
        "catalyst_data_status":"NSE_CORPORATE_ANNOUNCEMENTS",
        "early_watch_count":int(discovery.early_watch.sum()),
        "promotion_count":int(discovery.promoted.sum()),
        "promotion_block":None if discovery.promoted.any() else "No candidate cleared early-inflection + fundamental + catalyst + firewall gates",
        "validation_status":"CURRENT_PIT_LIVE_MODULE_RUN; HISTORICAL_WINNER_AUDIT_SEPARATE",
        "top_candidates":out.to_dict(orient="records"),
    }
