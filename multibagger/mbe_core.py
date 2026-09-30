import pandas as pd

def score_mbe_core(df):
    x=df.copy()

    def pct(s):
        return s.rank(pct=True)*100

    r21=x.get("ret_21d",pd.Series(float("nan"),index=x.index))
    r63=x.get("ret_63d",pd.Series(float("nan"),index=x.index))
    r126=x.get("ret_126d",pd.Series(float("nan"),index=x.index))

    # MBE is designed to detect transition, not merely reward stocks that already ran.
    x["early_trend_component"]=(
        pct(r21.fillna(-1e9))*.45+
        pct(r63.fillna(-1e9))*.35+
        pct(r126.fillna(-1e9))*.20
    )
    x["acceleration_component"]=pct(
        r21.fillna(-1e9)-r63.fillna(-1e9)/3.0
    )

    # Maturity is a diagnostic/penalty, not a hard rejection. Continuation leaders
    # can survive, but they should not crowd out earlier transitions.
    r252=x.get("ret_252d",pd.Series(float("nan"),index=x.index))
    x["maturity_flag"]=(
        (r126.fillna(0)>1.00)|
        (r252.fillna(0)>2.00)
    )
    x["maturity_penalty"]=x["maturity_flag"].astype(float)*15.0

    x["earnings_component"]=x.get(
        "earnings_inflection",pd.Series(50,index=x.index)
    ).fillna(50)
    x["balance_component"]=x.get(
        "balance_sheet_runway",pd.Series(50,index=x.index)
    ).fillna(50)

    x["mbe_score"]=(
        x["fundamental_score"].fillna(0)*.45+
        x["earnings_component"]*.20+
        x["early_trend_component"]*.12+
        x["acceleration_component"]*.08+
        x["balance_component"]*.15-
        x["maturity_penalty"]
    )
    x["mbe_stage"]=x.apply(
        lambda r: "MATURE_CONTINUATION" if bool(r["maturity_flag"]) else (
            "ACCELERATING" if float(r["acceleration_component"])>=70 else "EARLY"
        ),
        axis=1,
    )
    return x
