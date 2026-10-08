import json, os
import pandas as pd
from core.market_data import nse_cross_section
from core.pit_store import load_facts
from multibagger.modules import score_fundamentals
from multibagger.early_inflection import score_early_inflection
from multibagger.firewall import apply_trap_firewall
from multibagger.mbe_core import score_mbe_core

WINNERS=["STLTECH","HFCL","WELCORP","DIACABS","CUPID","SIGMAADV","YASHO","INDOTECH","BLISSGVS","GKENERGY","PREMIERPOLY","RAJOOENG","WEBELSOLAR","VIDYAWIRES","LENSKART","GVT&D","BIRLACOT","JTLDEFENCE","KETOMOTORS","AHLWEST","GBLINFRA","MRUGESH","RAYMOND","TBZ","MOREPENLAB","SARAUTO","KABRAEXTRU"]
DATES=["2026-02-06","2026-03-06","2026-04-06","2026-05-06","2026-06-06","2026-07-06"]
MODS=["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]

def pct(s): return s.rank(pct=True)*100

def run_date(d):
    x=nse_cross_section(d)
    if x.empty:return pd.DataFrame(),pd.DataFrame()
    for c in ["ret_21d","ret_63d","ret_126d","ret_252d","avg_turnover_60d"]: x[c]=pd.to_numeric(x.get(c),errors="coerce")
    x=x[x.avg_turnover_60d.ge(2_000_000)&x.ret_63d.notna()].copy()
    x["blind_score"]=pct(x.ret_21d.fillna(-1e9))*.30+pct(x.ret_63d.fillna(-1e9))*.30+pct(x.ret_126d.fillna(-1e9))*.25+pct(x.avg_turnover_60d.fillna(0))*.15
    disc=x.sort_values("blind_score",ascending=False).head(min(500,len(x))).copy()
    facts=load_facts(d); facts=facts[facts.symbol.isin(disc.symbol)] if not facts.empty else pd.DataFrame()
    pit=set(facts.symbol.astype(str)) if not facts.empty else set()
    fs=score_fundamentals(disc,facts.to_dict("records") if not facts.empty else [])
    if not fs.empty: disc=disc.merge(fs,on="symbol",how="left",suffixes=("","_fund"))
    for c in MODS:
        if c not in disc:disc[c]=float("nan")
    disc["fundamental_score"]=disc[MODS].mean(axis=1,skipna=True)
    disc["fundamental_module_count"]=disc[MODS].notna().sum(axis=1)
    disc["pit_verified"]=disc.symbol.astype(str).isin(pit)
    fp=disc[disc.fundamental_score.notna()&disc.fundamental_module_count.ge(3)&pd.to_numeric(disc.fundamental_evidence,errors="coerce").ge(.45)].sort_values("fundamental_score",ascending=False).head(200).copy()
    if fp.empty:return disc,fp
    ei=score_early_inflection(fp,fp,{})
    for c in ["earnings_inflection","operating_leverage","cash_inflection","balance_sheet_runway"]:
        if c in ei:fp[c]=ei[c].values
    fp=score_mbe_core(fp)
    fw=apply_trap_firewall(fp);fp["trap_firewall_pass"]=fw["trap_firewall_pass"]
    fp=fp.sort_values(["trap_firewall_pass","mbe_score"],ascending=False).copy()
    fp["mbe_rank"]=range(1,len(fp)+1);fp["decision_date"]=d
    return disc,fp

def contrib(z):
    if z is None:return {}
    return {"contrib_fundamental":float(z.get("fundamental_score",0))*.45,
            "contrib_earnings":float(z.get("earnings_component",50))*.20,
            "contrib_early_trend":float(z.get("early_trend_component",0))*.12,
            "contrib_acceleration":float(z.get("acceleration_component",0))*.08,
            "contrib_balance":float(z.get("balance_component",50))*.15,
            "contrib_maturity_penalty":-float(z.get("maturity_penalty",0))}

def main():
    winner_rows=[];control_rows=[];coverage=[]
    for d in DATES:
        disc,pool=run_date(d)
        if disc.empty:continue
        for s in WINNERS:
            e=disc[disc.symbol==s];f=pool[pool.symbol==s];r=f if not f.empty else e
            row={"decision_date":d,"symbol":s,"discovery":not e.empty,"fundamental":not f.empty,"universe":not e.empty}
            if not r.empty:
                z=r.iloc[0]
                for c in ["fundamental_module_count","fundamental_evidence","pit_verified","fundamental_score","earnings_component","early_trend_component","acceleration_component","balance_component","maturity_penalty","mbe_score","mbe_rank","trap_firewall_pass"]:row[c]=z.get(c)
                row.update(contrib(z))
            winner_rows.append(row)
        # Matched controls: same decision date, same fundamental-module count, and nearest fundamental score.
        eligible=pool[~pool.symbol.isin(WINNERS)].copy()
        for s in WINNERS:
            w=pool[pool.symbol==s]
            if w.empty:continue
            z=w.iloc[0]; m=eligible[eligible.fundamental_module_count.eq(z.fundamental_module_count)].copy()
            if m.empty:m=eligible.copy()
            m["dist"]=(pd.to_numeric(m.fundamental_score,errors="coerce")-float(z.fundamental_score)).abs()+((pd.to_numeric(m.ret_126d,errors="coerce")-float(z.ret_126d)).abs() if "ret_126d" in m else 0)
            for _,c in m.nsmallest(3,"dist").iterrows():
                row={"decision_date":d,"winner_symbol":s,"symbol":c.symbol,"match_distance":float(c["dist"])}
                row.update(contrib(c));row["mbe_score"]=c.get("mbe_score");row["fundamental_score"]=c.get("fundamental_score")
                control_rows.append(row)
        coverage.append({"decision_date":d,"discovery_count":len(disc),"pit_symbols":int(disc.pit_verified.sum()),"fundamental_eligible":len(pool),
                         "winner_discovery_hits":int(disc.symbol.isin(WINNERS).sum()),"winner_fundamental_hits":int(pool.symbol.isin(WINNERS).sum()),
                         "pit_rule":"load_facts(as_of) filters available_at <= decision date"})
    wr=pd.DataFrame(winner_rows);cr=pd.DataFrame(control_rows);cov=pd.DataFrame(coverage)
    attribution={}
    for c in ["contrib_fundamental","contrib_earnings","contrib_early_trend","contrib_acceleration","contrib_balance","contrib_maturity_penalty"]:
        w=pd.to_numeric(wr.get(c),errors="coerce");q=pd.to_numeric(cr.get(c),errors="coerce")
        attribution[c]={"winner_mean":float(w.mean()) if w.notna().any() else None,"matched_control_mean":float(q.mean()) if q.notna().any() else None,
                        "winner_minus_control":float(w.mean()-q.mean()) if w.notna().any() and q.notna().any() else None}
    out={"status":"MBE_COMPONENT_ATTRIBUTION_COMPLETE","method":"Blind historical full-universe scoring; no current Chartink/Screener; PIT facts constrained by available_at cutoff; 3 nearest matched controls per winner/date",
         "dates":DATES,"coverage":cov.to_dict("records"),"winner_component_attribution":attribution,
         "winner_rows":wr.to_dict("records"),"matched_control_sample":cr.to_dict("records")}
    os.makedirs("reports",exist_ok=True);json.dump(out,open("reports/mbe-component-attribution.json","w"),indent=2,default=str);print(json.dumps(out,indent=2,default=str))
if __name__=="__main__":main()
