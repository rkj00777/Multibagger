import pandas as pd
from core.market_data import nse_cross_section
from core.pit_store import load_facts, coverage as pit_coverage
from core.catalyst import NSECatalyst, catalyst_score
from multibagger.modules import score_fundamentals
from multibagger.early_inflection import score_early_inflection
from multibagger.firewall import apply_trap_firewall

VERSION="4.2.0-pit-plus-public-discovery"

def _pct(s): return s.rank(pct=True)*100

def run(as_of:str):
    df=nse_cross_section(as_of)
    if df.empty:
        return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"status":"DATA_UNAVAILABLE"}

    for c in ["ret_63d","ret_126d","ret_252d","avg_turnover_60d","pct_off_high"]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    pct=lambda s:s.rank(pct=True)*100
    df["trend_score"]=pct(df.ret_63d.fillna(-1e9))*.25+pct(df.ret_126d.fillna(-1e9))*.35+pct(df.ret_252d.fillna(-1e9))*.40
    df["liquidity_score"]=pct(df.avg_turnover_60d.fillna(0))
    df["discovery_score"]=df.trend_score*.75+df.liquidity_score*.25
    eligible=df[(df.avg_turnover_60d>=2_000_000)&df.ret_126d.notna()].copy()

    n_universe=len(df)
    n_discovery=min(max(100,int(n_universe*0.10)),2000)
    discovery=eligible.sort_values(["discovery_score","liquidity_score"],ascending=False).head(n_discovery).copy()

    pit=load_facts(as_of)
    pit_stats=pit_coverage(pit)
    pit_facts=pit[pit.symbol.isin(discovery.symbol)] if not pit.empty else pd.DataFrame()
    pit_records=pit_facts.to_dict(orient="records") if not pit_facts.empty else []

    # PIT is authoritative whenever available. For the live discovery funnel only,
    # fill missing symbols with current public fundamentals. These records are explicitly
    # tagged NON_PIT and can never by themselves constitute historical validation.
    pit_symbols=set(pit_facts.symbol.astype(str).unique()) if not pit_facts.empty else set()
    missing_symbols=[s for s in discovery.symbol.astype(str).tolist() if s not in pit_symbols]
    public_records=[]
    public_status="NOT_USED"
    if missing_symbols:
        try:
            from core.screener_fundamentals import ScreenerFundamentals
            public_records=ScreenerFundamentals(workers=8).batch(missing_symbols,as_of)
            public_status="SCREENER_PUBLIC_FALLBACK_NON_PIT" if public_records else "PUBLIC_FALLBACK_EMPTY"
        except Exception:
            public_status="PUBLIC_FALLBACK_ERROR"

    records=pit_records+public_records
    source_symbols=set(str(r.get("symbol")) for r in public_records)
    source_status="NSE_XBRL_PIT_STORE_PLUS_PUBLIC_FALLBACK" if public_records else "NSE_XBRL_PIT_STORE"

    if not records:
        return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"status":"DATA_LAYER_INCOMPLETE",
                "universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),"discovery_pool":int(len(discovery)),
                "fundamental_pool":0,"mbe_pool":0,"final_shortlist":0,"fundamental_facts":0,
                "fundamental_data_status":"NO_PIT_OR_PUBLIC_FUNDAMENTALS","pit_coverage":pit_stats,
                "promotion_count":0,"promotion_block":"No production promotion until fundamentals are available",
                "validation_status":"CURRENT_LIVE_DISCOVERY; HISTORICAL_PIT_VALIDATION_SEPARATE"}

    fs=score_fundamentals(discovery,records)
    discovery=discovery.merge(fs,on="symbol",how="left")
    discovery["fundamental_score"]=discovery[["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]].mean(axis=1,skipna=False)
    discovery["pit_verified"]=discovery.symbol.astype(str).isin(pit_symbols)

    # Adaptive: retain up to 20% of discovery with complete fundamental scores.
    n_fundamental=min(max(30,int(len(discovery)*0.20)),500)
    fundamental_pool=discovery[discovery.fundamental_score.notna()&discovery.fundamental_evidence.ge(.50)].sort_values(
        ["fundamental_score","fundamental_evidence","discovery_score"],ascending=False).head(n_fundamental).copy()

    if fundamental_pool.empty:
        return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"status":"LIVE_SCAN_COMPLETE",
                "universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),"discovery_pool":int(len(discovery)),
                "fundamental_pool":0,"mbe_pool":0,"final_shortlist":0,"fundamental_facts":int(len(records)),
                "fundamental_data_status":source_status,"pit_coverage":pit_stats,
                "promotion_count":0,"promotion_block":"No candidates cleared fundamental confirmation",
                "validation_status":"CURRENT_LIVE_DISCOVERY; HISTORICAL_PIT_VALIDATION_SEPARATE"}

    ei=score_early_inflection(fundamental_pool,fundamental_pool,{})
    for c in ["earnings_inflection","operating_leverage","cash_inflection","balance_sheet_runway"]:
        if c in ei: fundamental_pool[c]=ei[c].values

    from multibagger.mbe_core import score_mbe_core
    fundamental_pool=score_mbe_core(fundamental_pool)
    pre=apply_trap_firewall(fundamental_pool)
    fundamental_pool["trap_flags"]=pre["trap_flags"]
    fundamental_pool["trap_firewall_pass"]=pre["trap_firewall_pass"]

    n_mbe=min(max(10,int(len(fundamental_pool)*0.40)),100)
    mbe_pool=fundamental_pool.sort_values(["trap_firewall_pass","mbe_score"],ascending=False).head(n_mbe).copy()

    cats=NSECatalyst(workers=4).batch(mbe_pool.symbol.tolist(),as_of)
    catmap={}
    for item in cats:
        sym=item.get("symbol") or item.get("sym")
        if sym: catmap.setdefault(sym,[]).append(item)
    mbe_pool["catalyst_score"]=mbe_pool.symbol.map(lambda s:catalyst_score(catmap.get(s,[])))

    ei_final=score_early_inflection(mbe_pool,mbe_pool,catmap)
    for c in ["order_visibility","capacity_inflection","structural_theme","early_inflection_score","early_stage"]:
        if c in ei_final: mbe_pool[c]=ei_final[c].values

    final=mbe_pool[mbe_pool.trap_firewall_pass&mbe_pool.catalyst_score.ge(55)].sort_values(
        ["catalyst_score","mbe_score","fundamental_score"],ascending=False).head(8).copy()

    # Promotion is deliberately stricter and requires PIT verification.
    promoted=final[final.mbe_score.ge(65)&final.fundamental_score.ge(60)&final.fundamental_evidence.ge(.75)&
                   final.catalyst_score.ge(60)&final.trap_firewall_pass&final.pit_verified].copy()

    cols=[c for c in ["symbol","name","close","ret_63d","ret_126d","ret_252d","discovery_score",
        "fundamental_score","fundamental_evidence","pit_verified","mbe_score","catalyst_score",
        "earnings_inflection","order_visibility","capacity_inflection","structural_theme",
        "early_inflection_score","early_stage","trap_flags","trap_firewall_pass","promoted"] if c in final.columns]

    return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,
            "data_date":str(df.data_date.max())[:10],"status":"LIVE_SCAN_COMPLETE",
            "universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),"discovery_pool":int(len(discovery)),
            "fundamental_pool":int(len(fundamental_pool)),"mbe_pool":int(len(mbe_pool)),"final_shortlist":int(len(final)),
            "fundamental_facts":int(len(records)),
            "fundamental_data_status":source_status,
            "public_fallback_symbols":int(len(source_symbols)),
            "pit_verified_discovery_symbols":int(len(pit_symbols)),
            "pit_rule":"Only facts with NSE exchange availability/broadcast timestamp <= as_of are PIT eligible",
            "pit_coverage":pit_stats,"catalyst_data_status":"NSE_CORPORATE_ANNOUNCEMENTS_FINALIST_ONLY",
            "promotion_count":int(len(promoted)),
            "promotion_block":None if len(promoted) else "No finalist cleared all gates with PIT verification",
            "funnel":{"available_universe":int(n_universe),"discovery":int(len(discovery)),
                     "fundamental":int(len(fundamental_pool)),"mbe":int(len(mbe_pool)),
                     "catalyst_finalists":int(len(final))},
            "final_candidates":final[cols].to_dict(orient="records"),
            "promoted_candidates":promoted[cols].to_dict(orient="records"),
            "validation_status":"CURRENT_LIVE_DISCOVERY; PIT_PROMOTION_ONLY; HISTORICAL_WINNER_AUDIT_SEPARATE"}
