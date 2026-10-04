import json, os
import pandas as pd
from core.market_data import nse_cross_section
from core.pit_store import load_facts
from multibagger.modules import score_fundamentals
from multibagger.early_inflection import score_early_inflection
from multibagger.firewall import apply_trap_firewall
from multibagger.mbe_core import score_mbe_core

WINNERS = ["STLTECH","HFCL","WELCORP","DIACABS","CUPID","SIGMAADV","YASHO","INDOTECH","BLISSGVS","GKENERGY","PREMIERPOLY","RAJOOENG","WEBELSOLAR","VIDYAWIRES","LENSKART","GVT&D","BIRLACOT","JTLDEFENCE","KETOMOTORS","AHLWEST","GBLINFRA","MRUGESH","RAYMOND","TBZ","MOREPENLAB","SARAUTO","KABRAEXTRU"]
DATES = ["2026-02-06","2026-03-06","2026-04-06","2026-05-06","2026-06-06","2026-07-06"]

def pct(s): return s.rank(pct=True) * 100

def run_date(d):
    x = nse_cross_section(d)
    if x.empty: return pd.DataFrame(), pd.DataFrame()
    for c in ["ret_21d","ret_63d","ret_126d","ret_252d","avg_turnover_60d"]:
        x[c] = pd.to_numeric(x.get(c), errors="coerce")
    x = x[x.avg_turnover_60d.ge(2_000_000) & x.ret_63d.notna()].copy()
    x["blind_score"] = pct(x.ret_21d.fillna(-1e9))*.30 + pct(x.ret_63d.fillna(-1e9))*.30 + pct(x.ret_126d.fillna(-1e9))*.25 + pct(x.avg_turnover_60d.fillna(0))*.15
    disc = x.sort_values("blind_score", ascending=False).head(min(500,len(x))).copy()
    disc["trend_score"] = disc["blind_score"]
    facts = load_facts(d)
    facts = facts[facts.symbol.isin(disc.symbol)] if not facts.empty else pd.DataFrame()
    pit = set(facts.symbol.astype(str)) if not facts.empty else set()
    fs = score_fundamentals(disc, facts.to_dict("records") if not facts.empty else [])
    if not fs.empty:
        disc = disc.merge(fs, on="symbol", how="left", suffixes=("","_fund"))
    mods=["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]
    for c in mods:
        if c not in disc: disc[c]=float("nan")
    disc["fundamental_score"]=disc[mods].mean(axis=1,skipna=True)
    disc["fundamental_module_count"]=disc[mods].notna().sum(axis=1)
    disc["pit_verified"]=disc.symbol.astype(str).isin(pit)
    fp=disc[disc.fundamental_score.notna() & disc.fundamental_module_count.ge(3) & pd.to_numeric(disc.fundamental_evidence,errors="coerce").ge(.45)].sort_values("fundamental_score",ascending=False).head(200).copy()
    if fp.empty: return disc, fp
    ei=score_early_inflection(fp,fp,{})
    for c in ["earnings_inflection","operating_leverage","cash_inflection","balance_sheet_runway"]:
        if c in ei: fp[c]=ei[c].values
    fp=score_mbe_core(fp)
    fw=apply_trap_firewall(fp)
    fp["trap_firewall_pass"]=fw["trap_firewall_pass"]
    fp=fp.sort_values(["trap_firewall_pass","mbe_score"],ascending=False).head(100).copy()
    fp["mbe_rank"]=range(1,len(fp)+1)
    fp["decision_date"]=d
    return disc, fp

def main():
    winner_rows=[]; control_rows=[]; coverage=[]
    for d in DATES:
        disc, pool=run_date(d)
        if disc.empty: continue
        for s in WINNERS:
            e=disc[disc.symbol==s]
            f=pool[pool.symbol==s]
            r=f if not f.empty else e
            row={"decision_date":d,"symbol":s,"discovery":not e.empty,"fundamental":not f.empty,
                 "universe":not e.empty,"fundamental_module_count":None,"fundamental_evidence":None,"pit_verified":None}
            if not r.empty:
                z=r.iloc[0]
                for c in ["fundamental_module_count","fundamental_evidence","pit_verified","fundamental_score","earnings_component","early_trend_component","acceleration_component","balance_component","maturity_penalty","mbe_score","mbe_rank","trap_firewall_pass"]:
                    row[c]=z.get(c)
                if "fundamental_score" in z and pd.notna(z.get("fundamental_score")):
                    row["contrib_fundamental"]=float(z.get("fundamental_score",0))*.45
                    row["contrib_earnings"]=float(z.get("earnings_component",50))*.20
                    row["contrib_early_trend"]=float(z.get("early_trend_component",0))*.12
                    row["contrib_acceleration"]=float(z.get("acceleration_component",0))*.08
                    row["contrib_balance"]=float(z.get("balance_component",50))*.15
                    row["contrib_maturity_penalty"]=-float(z.get("maturity_penalty",0))
            winner_rows.append(row)
        if not pool.empty:
            controls=pool[~pool.symbol.isin(WINNERS)].head(50)
            for _,z in controls.iterrows():
                control_rows.append({"decision_date":d,"symbol":z.symbol,"mbe_score":z.get("mbe_score"),
                    "fundamental_score":z.get("fundamental_score"),"earnings_component":z.get("earnings_component"),
                    "early_trend_component":z.get("early_trend_component"),"acceleration_component":z.get("acceleration_component"),
                    "balance_component":z.get("balance_component"),"maturity_penalty":z.get("maturity_penalty"),
                    "trap_firewall_pass":z.get("trap_firewall_pass")})
        coverage.append({"decision_date":d,"discovery_count":len(disc),"pit_symbols":int(disc.pit_verified.sum()),
                         "fundamental_eligible":len(pool),"winner_discovery_hits":sum(disc.symbol.isin(WINNERS)),
                         "winner_fundamental_hits":sum(pool.symbol.isin(WINNERS)) if not pool.empty else 0})
    wr=pd.DataFrame(winner_rows); cr=pd.DataFrame(control_rows); cov=pd.DataFrame(coverage)
    attribution={}
    for c in ["contrib_fundamental","contrib_earnings","contrib_early_trend","contrib_acceleration","contrib_balance","contrib_maturity_penalty"]:
        v=pd.to_numeric(wr.get(c),errors="coerce")
        attribution[c]={"winner_mean":float(v.mean()) if v.notna().any() else None,"winner_median":float(v.median()) if v.notna().any() else None}
        cv=pd.to_numeric(cr.get(c.replace("contrib_","")),errors="coerce") if c.replace("contrib_","") in cr else pd.Series(dtype=float)
        attribution[c]["control_mean"]=None
    # Explicit control component means.
    for src,dst in [("fundamental_score","contrib_fundamental"),("earnings_component","contrib_earnings"),("early_trend_component","contrib_early_trend"),("acceleration_component","contrib_acceleration"),("balance_component","contrib_balance"),("maturity_penalty","contrib_maturity_penalty")]:
        v=pd.to_numeric(cr[src],errors="coerce")
        attribution[dst]["control_mean"]=float((v*(.45 if src=="fundamental_score" else .20 if src=="earnings_component" else .12 if src=="early_trend_component" else .08 if src=="acceleration_component" else .15 if src=="balance_component" else -1)).mean()) if v.notna().any() else None
    out={"status":"MBE_COMPONENT_ATTRIBUTION_COMPLETE","method":"Blind historical full-universe scoring; no current Chartink/Screener; PIT facts constrained by available_at cutoff",
         "dates":DATES,"coverage":cov.to_dict("records"),
         "winner_component_attribution":attribution,
         "winner_rows":wr.to_dict("records"),"control_sample":cr.to_dict("records")}
    os.makedirs("reports",exist_ok=True)
    json.dump(out,open("reports/mbe-component-attribution.json","w"),indent=2,default=str)
    print(json.dumps(out,indent=2,default=str))

if __name__=="__main__": main()
