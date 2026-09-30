import pandas as pd

def score_mbe_core(df):
    x=df.copy()
    def pct(s):
        return s.rank(pct=True)*100
    x["trend_component"] = (
        pct(x["ret_63d"].fillna(-1e9))*.25 +
        pct(x["ret_126d"].fillna(-1e9))*.35 +
        pct(x["ret_252d"].fillna(-1e9))*.40
    )
    x["earnings_component"] = (
        x.get("earnings_inflection", pd.Series(50,index=x.index)).fillna(50)
    )
    x["balance_component"] = (
        x.get("balance_sheet_runway", pd.Series(50,index=x.index)).fillna(50)
    )
    x["mbe_score"] = (
        x["fundamental_score"].fillna(0)*.45 +
        x["earnings_component"]*.20 +
        x["trend_component"]*.20 +
        x["balance_component"]*.15
    )
    return x
