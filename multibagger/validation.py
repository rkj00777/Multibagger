import pandas as pd, numpy as np
from core.market_data import nse_cross_section
from core.nse_fundamentals import NSEFundamentals
from multibagger.modules import score_fundamentals

def validate(months,top_n=20,horizon_days=252):
    rows=[]; ing=NSEFundamentals(workers=6)
    for as_of in months:
        px=nse_cross_section(as_of)
        if px.empty: continue
        px["technical"]=px.ret_63d.rank(pct=True)*25+px.ret_126d.rank(pct=True)*35+px.ret_252d.rank(pct=True)*40
        pool=px[(px.avg_turnover_60d>=2_000_000)&px.ret_126d.notna()].nlargest(top_n,"technical")
        facts=ing.batch(pool.symbol.tolist(),as_of)
        fs=score_fundamentals(pool,facts)
        if fs.empty: continue
        # Validation is deliberately event-cohort based. Selection uses only information available at as_of.
        # Forward returns are supplied by the caller/data layer; this harness never uses post-event fundamentals.
        for _,r in fs.iterrows():
            rows.append({"decision_date":as_of,"symbol":r.symbol,"fundamental_score":r[["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]].mean(),"evidence":r.fundamental_evidence})
    out=pd.DataFrame(rows)
    if out.empty:return {"status":"NO_VALIDATION_OBSERVATIONS"}
    return {"status":"PIT_MODULE_OBSERVATIONS_READY","observations":int(len(out)),"decision_dates":int(out.decision_date.nunique()),"mean_fundamental_score":float(out.fundamental_score.mean()),"median_evidence":float(out.evidence.median()),"next_gate":"join forward 6/12/24-month returns from historical EOD and run decile lift/bootstrap"}
