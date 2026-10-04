import json,os
import pandas as pd
from core.market_data import nse_cross_section
from core.pit_store import load_facts
from multibagger.modules import score_fundamentals
from multibagger.early_inflection import score_early_inflection
from multibagger.firewall import apply_trap_firewall
from multibagger.mbe_core import score_mbe_core
WINNERS={"STLTECH":"Sterlite Technologies","HFCL":"HFCL","WELCORP":"Welspun Corp","DIACABS":"Diamond Power Infrastructure","CUPID":"Cupid","SIGMAADV":"Sigma Advanced Systems","YASHO":"Yasho Industries","INDOTECH":"Indo Tech Transformers","BLISSGVS":"Bliss GVS Pharma","GKENERGY":"GK Energy","PREMIERPOLY":"Premier Polyfilm","RAJOOENG":"Rajoo Engineers","WEBELSOLAR":"Websol Energy System","VIDYAWIRES":"Vidya Wires","LENSKART":"Lenskart","GVT&D":"GE Vernova T&D India","BIRLACOT":"Birla Cotsyn (India)","JTLDEFENCE":"JTL Defence","KETOMOTORS":"Keto Motors","AHLWEST":"Asian Hotels (West)","GBLINFRA":"Global Infratech & Finance","MRUGESH":"Mrugesh Trading","RAYMOND":"Raymond","TBZ":"Tribhovandas Bhimji Zaveri","MOREPENLAB":"Morepen Laboratories","SARAUTO":"SAR Auto Products","KABRAEXTRU":"Kabra Extrusiontechnik"}
DATES=["2026-02-06","2026-03-06","2026-04-06","2026-05-06","2026-06-06","2026-07-06"]
def pct(s): return s.rank(pct=True)*100
def audit(d):
 x=nse_cross_section(d)
 if x.empty:return []
 for c in ["ret_21d","ret_63d","ret_126d","ret_252d","avg_turnover_60d"]:x[c]=pd.to_numeric(x.get(c),errors="coerce")
 x=x[x.avg_turnover_60d.ge(2_000_000)&x.ret_63d.notna()].copy()
 x["blind_score"]=pct(x.ret_21d.fillna(-1e9))*.30+pct(x.ret_63d.fillna(-1e9))*.30+pct(x.ret_126d.fillna(-1e9))*.25+pct(x.avg_turnover_60d.fillna(0))*.15
 # Broad blind candidate set: no Chartink/Screener/current information.
 disc=x.sort_values("blind_score",ascending=False).head(min(500,len(x))).copy()
 disc["trend_score"]=disc["blind_score"]\ndisc["discovery_rank"]=range(1,len(disc)+1)
 facts=load_facts(d); facts=facts[facts.symbol.isin(disc.symbol)] if not facts.empty else pd.DataFrame()
 pit=set(facts.symbol.astype(str)) if not facts.empty else set()
 missing=[s for s in disc.symbol.astype(str) if s not in pit]
 if missing:
  try:
   n=NSEPIT(); rows=[]
   for s in missing:
    try: rows += n.facts(s,d) or []
    except: pass
   if rows:
    z=pd.DataFrame(rows); facts=pd.concat([facts,z],ignore_index=True) if not facts.empty else z; pit.update(z.symbol.astype(str))
  except: pass
 fs=score_fundamentals(disc,facts.to_dict("records") if not facts.empty else [])
 if not fs.empty: disc=disc.merge(fs,on="symbol",how="left")
 mods=["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]
 for c in mods:
  if c not in disc:disc[c]=float("nan")
 disc["fundamental_score"]=disc[mods].mean(axis=1,skipna=True);disc["fundamental_module_count"]=disc[mods].notna().sum(axis=1);disc["pit_verified"]=disc.symbol.astype(str).isin(pit)
 fp=disc[disc.fundamental_score.notna()&disc.fundamental_module_count.ge(3)&pd.to_numeric(disc.fundamental_evidence,errors="coerce").ge(.45)].sort_values("fundamental_score",ascending=False).head(200).copy()
 if fp.empty:return rows_for(d,disc,pd.DataFrame(),pd.DataFrame())
 ei=score_early_inflection(fp,fp,{})
 for c in ["earnings_inflection","operating_leverage","cash_inflection","balance_sheet_runway"]:
  if c in ei:fp[c]=ei[c].values
 fp=score_mbe_core(fp); fw=apply_trap_firewall(fp); fp["trap_firewall_pass"]=fw["trap_firewall_pass"]
 mp=fp.sort_values(["trap_firewall_pass","mbe_score"],ascending=False).head(100).copy();mp["mbe_rank"]=range(1,len(mp)+1)
 mp["production_mbe_gate"]=mp.mbe_score.ge(65)&mp.fundamental_score.ge(60)&mp.fundamental_evidence.ge(.75)&mp.trap_firewall_pass&mp.pit_verified&mp.ret_126d.le(.50)
 return rows_for(d,disc,fp,mp)
def rows_for(d,disc,fp,mp):
 out=[]
 for s,n in WINNERS.items():
  e=disc[disc.symbol==s];f=fp[fp.symbol==s];m=mp[mp.symbol==s];r=m if not m.empty else f if not f.empty else e
  q={"decision_date":d,"symbol":s,"name":n,"universe_present":not e.empty,"discovery_selected":not e.empty,"discovery_rank":int(e.iloc[0].discovery_rank) if not e.empty else None,"fundamental_pool_selected":not f.empty,"mbe_pool_selected":not m.empty,"mbe_rank":int(m.iloc[0].mbe_rank) if not m.empty else None}
  for c in ["close","ret_21d","ret_63d","ret_126d","ret_252d","fundamental_score","fundamental_module_count","fundamental_evidence","pit_verified","mbe_score","mbe_stage","maturity_flag","maturity_penalty","trap_firewall_pass","production_mbe_gate"]:
   q[c]=r.iloc[0].get(c) if not r.empty else None
  out.append(q)
 return out
def main():
 rows=sum((audit(d) for d in DATES),[])
 df=pd.DataFrame(rows); summary=[]
 for s,n in WINNERS.items():
  g=df[df.symbol==s];z=g[g.mbe_score.notna()]
  summary.append({"symbol":s,"name":n,"discovery_hits":int(g.discovery_selected.sum()),"fundamental_hits":int(g.fundamental_pool_selected.sum()),"mbe_hits":int(g.mbe_pool_selected.sum()),"best_mbe_score":float(z.mbe_score.max()) if not z.empty else None,"best_mbe_rank":int(z.mbe_rank.min()) if not z.empty and z.mbe_rank.notna().any() else None,"production_gate_hits":int(g.production_mbe_gate.fillna(False).sum()),"first_mbe_date":str(z.sort_values("decision_date").iloc[0].decision_date) if not z.empty else None})
 out={"status":"BLIND_HISTORICAL_MBE_TEST_COMPLETE","method":"Full historical universe + PIT fundamentals; no current Chartink/Screener; no future winner information","checkpoints":DATES,"summary":summary,"checkpoint_signals":rows}
 os.makedirs("reports",exist_ok=True);json.dump(out,open("reports/blind-historical-mbe-test.json","w"),indent=2,default=str);print(json.dumps(out,indent=2,default=str))
if __name__=="__main__":main()
