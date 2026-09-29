def apply_trap_firewall(df):
    x=df.copy()
    x["trap_flags"]=[[] for _ in range(len(x))]
    def flag(i,name):
        if name not in x.at[i,"trap_flags"]:x.at[i,"trap_flags"].append(name)
    for i,r in x.iterrows():
        if float(r.get("cash_conversion",50))<25:flag(i,"negative_cash_conversion")
        if float(r.get("governance_balance_sheet",50))<25:flag(i,"balance_sheet_stress")
        if float(r.get("earnings_acceleration",50))<20:flag(i,"earnings_collapse")
        if float(r.get("ret_126d",0))>1.0 and float(r.get("fundamental_score",0))<45:flag(i,"extended_without_fundamental_confirmation")
        if float(r.get("fundamental_evidence",0))<0.75:flag(i,"thin_fundamental_evidence")
        if pd_notna(r.get("pe_proxy")) and float(r.get("pe_proxy"))>100:flag(i,"extreme_earnings_multiple")
    x["trap_firewall_pass"]=x.trap_flags.map(len).eq(0)
    return x
def pd_notna(v):
    try:return v==v
    except:return False
