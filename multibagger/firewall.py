def apply_trap_firewall(df):
    x=df.copy()
    x["trap_flags"]=[[] for _ in range(len(x))]
    def num(v,default=0.0):
        try:
            if v is None: return default
            z=float(v)
            return default if z!=z else z
        except (TypeError,ValueError): return default
    def flag(i,name):
        if name not in x.at[i,"trap_flags"]: x.at[i,"trap_flags"].append(name)
    for i,r in x.iterrows():
        if num(r.get("cash_conversion"),50)<25: flag(i,"negative_cash_conversion")
        if num(r.get("governance_balance_sheet"),50)<25: flag(i,"balance_sheet_stress")
        if num(r.get("earnings_acceleration"),50)<20: flag(i,"earnings_collapse")
        if num(r.get("ret_126d"),0)>1.0 and num(r.get("fundamental_score"),0)<45: flag(i,"extended_without_fundamental_confirmation")
        if num(r.get("fundamental_evidence"),0)<0.75: flag(i,"thin_fundamental_evidence")
        pe=r.get("pe_proxy")
        if pe is not None:
            try:
                pv=float(pe)
                if pv==pv and pv>100: flag(i,"extreme_earnings_multiple")
            except (TypeError,ValueError):
                pass
    x["trap_firewall_pass"]=x.trap_flags.map(len).eq(0)
    return x
