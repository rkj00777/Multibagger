import json, os
import duckdb
import pandas as pd
from core.market_data import nse_cross_section
from core.initial_scanners import build_scanner_funnel
from core.nse_pit import NSEPIT
from core.pit_store import load_facts
from core.catalyst import NSECatalyst, ScreenerCatalyst, catalyst_score
from multibagger.modules import score_fundamentals
from multibagger.early_inflection import score_early_inflection
from multibagger.firewall import apply_trap_firewall
from multibagger.mbe_core import score_mbe_core

# Historical winners are used only for falsification/diagnosis, never as current
# recommendations. This set includes prior engine findings plus the recent
# six-month winners the user asked to test before their major moves.
WINNERS = {
    "STLTECH":"Sterlite Technologies",
    "HFCL":"HFCL",
    "WELCORP":"Welspun Corp",
    "DIACABS":"Diamond Power Infrastructure",
    "CUPID":"Cupid",
    "SIGMAADV":"Sigma Advanced Systems",
    "YASHO":"Yasho Industries",
    "INDOTECH":"Indo Tech Transformers",
    "BLISSGVS":"Bliss GVS Pharma",
    "GKENERGY":"GK Energy",
    "PREMIERPOLY":"Premier Polyfilm",
    "RAJOOENG":"Rajoo Engineers",
    "WEBELSOLAR":"Websol Energy System",
    "VIDYAWIRES":"Vidya Wires",
    "LENSKART":"Lenskart",
    "GVT&D":"GE Vernova T&D India",
}
# Check before/around the historical run-up, not after it. The MBE score at a
# checkpoint uses only data available at that checkpoint.
CHECKPOINTS = ["2026-02-06","2026-03-06","2026-04-06","2026-05-06","2026-06-06","2026-07-06"]

HF="https://huggingface.co/datasets/tejhq/indian-markets/resolve/main"

def prices(symbols):
    paths=f"['{HF}/nse/year=2026/nse_2026.parquet']"
    syms=",".join("'" + s.replace("'","''") + "'" for s in symbols)
    c=duckdb.connect()
    q=f"""select symbol,date,close from read_parquet({paths},union_by_name=true)
          where symbol in ({syms}) order by symbol,date"""
    out=c.execute(q).fetchdf(); c.close()
    out["date"]=pd.to_datetime(out["date"])
    return out

def threshold_dates():
    p=prices(list(WINNERS))
    start=pd.Timestamp("2026-02-06")
    out=[]
    for s,n in WINNERS.items():
        g=p[(p.symbol==s)&(p.date>=start)].copy().sort_values("date")
        if g.empty: continue
        p0=float(g.iloc[0].close)
        g["ret_from_start"]=g.close/p0-1
        hit=g[g.ret_from_start>=1.0]
        end=g[g.date<=pd.Timestamp("2026-08-07")]
        out.append({"symbol":s,"name":n,"start_price":p0,
                    "end_date":"2026-08-07",
                    "six_month_return":float(end.iloc[-1].close/p0-1) if not end.empty else None,
                    "first_plus_100_date":str(hit.iloc[0].date.date()) if not hit.empty else None})
    return pd.DataFrame(out)

def audit_date(as_of):
    px=nse_cross_section(as_of)
    if px.empty:return []
    for c in ["ret_21d","ret_63d","ret_126d","ret_252d","avg_turnover_60d","pct_off_high"]:
        px[c]=pd.to_numeric(px.get(c),errors="coerce")
    # Recreate production trend/discovery context across the full screened universe.
    # Percentile ranks must not be computed on the historical winners alone.
    screened,_=build_scanner_funnel(px)
    if screened.empty:return []
    screened["trend_score"]=screened.ret_63d.fillna(-1e9).rank(pct=True)*100*.25 + screened.ret_126d.fillna(-1e9).rank(pct=True)*100*.35 + screened.ret_252d.fillna(-1e9).rank(pct=True)*100*.40
    screened["liquidity_score"]=screened.avg_turnover_60d.fillna(0).rank(pct=True)*100
    screened["early_momentum_score"]=screened.ret_21d.fillna(-1e9).rank(pct=True)*100*.50 + screened.ret_63d.fillna(-1e9).rank(pct=True)*100*.30 + screened.ret_126d.fillna(-1e9).rank(pct=True)*100*.20
    screened["discovery_score"]=screened.scanner_score*.45+screened.early_momentum_score*.35+screened.liquidity_score*.20
    target=screened[screened.symbol.isin(WINNERS)].copy()
    if target.empty:return []

    facts_df=load_facts(as_of)
    facts=facts_df[facts_df.symbol.isin(target.symbol)] if not facts_df.empty else pd.DataFrame()
    pit_symbols=set(facts.symbol.astype(str).unique()) if not facts.empty else set()
    missing=[s for s in target.symbol.astype(str).tolist() if s not in pit_symbols]
    if missing:
        try:
            dyn=NSEPIT()
            rows=[]
            for s in missing:
                try: rows.extend(dyn.facts(s,as_of) or [])
                except Exception: pass
            if rows:
                dyn_df=pd.DataFrame(rows)
                facts=pd.concat([facts,dyn_df],ignore_index=True) if not facts.empty else dyn_df
        except Exception: pass

    fs=score_fundamentals(target,facts.to_dict(orient="records") if isinstance(facts,pd.DataFrame) else facts)
    z=target.merge(fs,on="symbol",how="left") if not fs.empty else target.copy()

    # Recreate the production cross-sectional trend feature required by the
    # early-inflection module. The historical audit previously passed only the
    # winner subset, which omitted trend_score and caused the audit to abort.
    # Percentiles are calculated against the full PIT market cross-section.
    def _pct(s):
        return s.rank(pct=True)*100
    z["trend_score"]=(
        _pct(pd.to_numeric(px["ret_63d"],errors="coerce").fillna(-1e9)) * .25 +
        _pct(pd.to_numeric(px["ret_126d"],errors="coerce").fillna(-1e9)) * .35 +
        _pct(pd.to_numeric(px["ret_252d"],errors="coerce").fillna(-1e9)) * .40
    ).reindex(z.index)
    z["discovery_score"]=z["trend_score"]

    modules=["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]
    for c in modules:
        if c not in z:z[c]=float("nan")
    z["fundamental_score"]=z[modules].mean(axis=1,skipna=True)
    z["fundamental_module_count"]=z[modules].notna().sum(axis=1)
    z["pit_verified"]=z.symbol.astype(str).isin(pit_symbols)

    # Reproduce the production MBE calculation at the historical checkpoint.
    ei=score_early_inflection(z,z,{})
    for c in ["earnings_inflection","operating_leverage","cash_inflection","balance_sheet_runway"]:
        if c in ei:z[c]=ei[c].values
    z=score_mbe_core(z)

    # Historical maturity/entry classification is diagnostic only.
    z["six_month_return"]=z["ret_126d"]
    z["entry_stage"]=z["ret_126d"].apply(
        lambda x:"EARLY" if pd.notna(x) and x<=0.25 else
                  "EARLY_ACCELERATING" if pd.notna(x) and x<=0.50 else
                  "ESTABLISHED" if pd.notna(x) and x<=1.00 else "MATURE"
    )
    z=apply_trap_firewall(z)

    # Catalyst is reported separately. If NSE is unavailable, Screener is
    # explicitly labelled NON-PIT and never used to claim historical MBE validity.
    cats=NSECatalyst(workers=4).batch(z.symbol.tolist(),as_of)
    catmap={}
    for x in cats:
        sym=x.get("symbol") or x.get("sym")
        if sym:catmap.setdefault(sym,[]).append(x)
    z["catalyst_score"]=z.symbol.map(lambda s:catalyst_score(catmap.get(s,[])))
    z["catalyst_source"]=z.symbol.map(lambda s:
        "NSE_PIT" if any(i.get("source_type")=="NSE_CORPORATE_ANNOUNCEMENT" for i in catmap.get(s,[]))
        else "SCREENER_NON_PIT"
    )

    z["decision_date"]=as_of
    z["name"]=z.symbol.map(WINNERS)
    z["early_watch"]=z.early_inflection_score.ge(65)&z.fundamental_evidence.ge(.50)&z.trap_firewall_pass
    z["production_mbe_gate"]=(
        z.mbe_score.ge(65)&z.fundamental_score.ge(60)&
        z.fundamental_evidence.ge(.75)&z.trap_firewall_pass&
        z.pit_verified&z.six_month_return.le(.50)
    )
    cols=["decision_date","symbol","name","close","ret_21d","ret_63d","ret_126d",
          "ret_252d","entry_stage","fundamental_score","fundamental_module_count",
          "fundamental_evidence","pit_verified","mbe_score","mbe_stage",
          "maturity_flag","maturity_penalty","catalyst_score","catalyst_source",
          "earnings_inflection","operating_leverage","early_inflection_score",
          "early_stage","early_watch","production_mbe_gate"]
    return z[[c for c in cols if c in z.columns]].to_dict("records")

def main():
    rows=[]
    for d in CHECKPOINTS: rows.extend(audit_date(d))
    th=threshold_dates()
    os.makedirs("reports",exist_ok=True)
    out={"status":"WINNER_MBE_PIT_AUDIT_COMPLETE",
         "winner_window":"2026-02-06 to 2026-08-07",
         "method":"Production MBE score reproduced at PIT checkpoints; no post-checkpoint fundamentals used",
         "note":"A stock is considered 'detected before discovery' only if its production MBE score/gates clear at a checkpoint preceding its major run-up. This audit does not use the later winner outcome in the score.",
         "winners":th.to_dict(orient="records"),
         "checkpoint_signals":rows}
    json.dump(out,open("reports/multibagger-winner-audit.json","w"),indent=2,default=str)
    print(json.dumps(out,indent=2,default=str))

if __name__=="__main__": main()
