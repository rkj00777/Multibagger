import pandas as pd

def apply_trap_firewall(df):
    x=df.copy()
    rules={
      "negative_cfo": x.get("cfo_score",pd.Series(50,index=x.index)).fillna(50)<25,
      "balance_sheet_stress": x.get("governance_balance_sheet",pd.Series(50,index=x.index)).fillna(50)<25,
      "earnings_collapse": x.get("earnings_acceleration",pd.Series(50,index=x.index)).fillna(50)<20,
      "extended_without_confirmation": (x.get("ret_126d",0)>1.0) & (x.get("fundamental_score",0)<45),
      "evidence_thin": x.get("fundamental_evidence",0)<0.75,
    }
    flags=[]
    for i in x.index:
        f=[k for k,v in rules.items() if bool(v.loc[i])]
        flags.append(f)
    x["trap_flags"]=flags
    x["trap_firewall_pass"]=x.trap_flags.map(len).eq(0)
    return x
