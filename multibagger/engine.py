import pandas as pd
from core.market_data import nse_cross_section
VERSION="2.0.0-live-data-honest"
def _pct(s): return s.rank(pct=True)*100
def run(as_of:str):
    df=nse_cross_section(as_of)
    if df.empty: return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"status":"DATA_UNAVAILABLE","reason":"No NSE EOD rows available"}
    for c in ["ret_63d","ret_126d","ret_252d","avg_turnover_60d","pct_off_high"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    df["trend_score"]=_pct(df.ret_63d.fillna(-1e9))*.25+_pct(df.ret_126d.fillna(-1e9))*.35+_pct(df.ret_252d.fillna(-1e9))*.40
    df["liquidity_score"]=_pct(df.avg_turnover_60d.fillna(0)); df["discovery_score"]=df.trend_score*.75+df.liquidity_score*.25
    eligible=df[(df.avg_turnover_60d>=2_000_000)&df.ret_126d.notna()].copy(); top=eligible.sort_values(["discovery_score","ret_126d"],ascending=False).head(30)
    cols=["symbol","name","close","avg_turnover_60d","ret_63d","ret_126d","ret_252d","pct_off_high","trend_score","discovery_score"]; top=top[cols].round(4)
    return {"engine":"Multibagger","version":VERSION,"as_of_requested":as_of,"data_date":str(df.data_date.max())[:10],"status":"LIVE_SCAN_COMPLETE","universe_rows":int(len(df)),"liquid_eligible":int(len(eligible)),"fundamental_data_status":"UNAVAILABLE_IN_FREE_SOURCE","fundamental_modules_verified":0,"promotion_count":0,"promotion_block":"HARD_BLOCK: no dated PIT fundamental evidence; no synthetic values used","top_discovery_watchlist":top.to_dict(orient="records"),"data_source":"TejHQ/Hugging Face NSE bhavcopy; public EOD; no paid provider","validation_status":"LIVE_DISCOVERY_ONLY_NOT_EMPIRICALLY_VALIDATED"}
