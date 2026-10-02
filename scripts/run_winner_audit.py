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
    # Reproduce the production pipeline exactly through the MBE stage, then
    # extract the historical winners. This is deliberately NOT a winner-only
    # calculation: percentile ranks and top-N selection must be formed from the
    # full historical cross-section to avoid giving winners artificial scores.
    px=nse_cross_section(as_of)
    if px.empty:
        return []
    for c in ["ret_21d","ret_63d","ret_126d","ret_252d","avg_turnover_60d","pct_off_high"]:
        px[c]=pd.to_numeric(px.get(c),errors="coerce")

    screened,_=build_scanner_funnel(px)
    if screened.empty:
        return []

    def pct(s):
        return s.rank(pct=True)*100

    screened["trend_score"]=(
        pct(screened.ret_63d.fillna(-1e9))*.25+
        pct(screened.ret_126d.fillna(-1e9))*.35+
        pct(screened.ret_252d.fillna(-1e9))*.40
    )
    screened["liquidity_score"]=pct(screened.avg_turnover_60d.fillna(0))
    screened["early_momentum_score"]=(
        pct(screened.ret_21d.fillna(-1e9))*.50+
        pct(screened.ret_63d.fillna(-1e9))*.30+
        pct(screened.ret_126d.fillna(-1e9))*.20
    )
    screened["discovery_score"]=(
        screened.scanner_score*.45+
        screened.early_momentum_score*.35+
        screened.liquidity_score*.20
    )

    eligible=screened[
        (screened.avg_turnover_60d>=2_000_000)&screened.ret_63d.notna()
    ].copy()
    n_scanner=len(screened)
    n_discovery=min(max(100,int(max(1,n_scanner)*0.15)),500)
    discovery=eligible.sort_values(
        ["discovery_score","scanner_score"],ascending=False
    ).head(n_discovery).copy()

    discovery["six_month_return"]=discovery["ret_126d"]
    discovery["six_month_runup_pass"]=discovery["ret_126d"].le(.50)
    discovery["entry_stage"]=discovery["ret_126d"].apply(
        lambda x:"EARLY" if pd.notna(x) and x<=0.25 else
                  "EARLY_ACCELERATING" if pd.notna(x) and x<=0.50 else
                  "ESTABLISHED" if pd.notna(x) and x<=1.00 else "MATURE"
    )
    discovery["discovery_rank"]=range(1,len(discovery)+1)

    winner_symbols=set(WINNERS)
    target_screened=screened[screened.symbol.isin(winner_symbols)].copy()
    target_discovery=discovery[discovery.symbol.isin(winner_symbols)].copy()

    # Historical PIT fundamentals are authoritative. Dynamic NSE PIT is used
    # only to fill missing PIT symbols; public current fundamentals are never
    # substituted in this historical validation.
    facts_df=load_facts(as_of)
    facts=facts_df[
        facts_df.symbol.isin(discovery.symbol)
    ] if not facts_df.empty else pd.DataFrame()
    pit_symbols=set(facts.symbol.astype(str).unique()) if not facts.empty else set()

    missing=[
        s for s in discovery.symbol.astype(str).tolist()
        if s not in pit_symbols
    ]
    if missing:
        try:
            dyn=NSEPIT()
            rows=[]
            for s in missing:
                try:
                    rows.extend(dyn.facts(s,as_of) or [])
                except Exception:
                    pass
            if rows:
                dyn_df=pd.DataFrame(rows)
                facts=pd.concat([facts,dyn_df],ignore_index=True) if not facts.empty else dyn_df
                pit_symbols.update(dyn_df.symbol.astype(str).unique())
        except Exception:
            pass

    records=facts.to_dict(orient="records") if not facts.empty else []
    fs=score_fundamentals(discovery,records) if records else pd.DataFrame()
    discovery=discovery.merge(fs,on="symbol",how="left") if not fs.empty else discovery.copy()

    modules=[
        "valuation_gap","earnings_acceleration","cash_conversion",
        "reinvestment_roic","governance_balance_sheet"
    ]
    for c in modules:
        if c not in discovery:
            discovery[c]=float("nan")
    discovery["fundamental_score"]=discovery[modules].mean(axis=1,skipna=True)
    discovery["fundamental_module_count"]=discovery[modules].notna().sum(axis=1)
    discovery["pit_verified"]=discovery.symbol.astype(str).isin(pit_symbols)

    # Exact production fundamental gate/pool sizing.
    n_fundamental=min(max(30,int(len(discovery)*0.20)),200)
    fundamental_pool=discovery[
        discovery.fundamental_score.notna()&
        discovery.fundamental_module_count.ge(3)&
        discovery.fundamental_evidence.ge(.45)
    ].sort_values(
        ["fundamental_score","fundamental_evidence","discovery_score"],
        ascending=False
    ).head(n_fundamental).copy()

    if fundamental_pool.empty:
        return _audit_rows_without_mbe(
            as_of,target_screened,target_discovery,discovery
        )

    # Production early-inflection and MBE calculations operate on the full
    # fundamental pool, not on historical winners alone.
    ei=score_early_inflection(fundamental_pool,fundamental_pool,{})
    for c in [
        "earnings_inflection","operating_leverage",
        "cash_inflection","balance_sheet_runway"
    ]:
        if c in ei:
            fundamental_pool[c]=ei[c].values

    fundamental_pool=score_mbe_core(fundamental_pool)
    pre=apply_trap_firewall(fundamental_pool)
    fundamental_pool["trap_flags"]=pre["trap_flags"]
    fundamental_pool["trap_firewall_pass"]=pre["trap_firewall_pass"]

    n_mbe=min(max(10,int(len(fundamental_pool)*0.40)),100)
    mbe_pool=fundamental_pool.sort_values(
        ["trap_firewall_pass","mbe_score"],ascending=False
    ).head(n_mbe).copy()
    mbe_pool["mbe_rank"]=range(1,len(mbe_pool)+1)

    # Catalyst is reported only for names that actually reach the production
    # MBE pool. It is not allowed to alter the historical MBE score.
    catmap={}
    try:
        cats=NSECatalyst(workers=4).batch(mbe_pool.symbol.tolist(),as_of)
        for item in cats:
            sym=item.get("symbol") or item.get("sym")
            if sym:
                catmap.setdefault(sym,[]).append(item)
    except Exception:
        pass
    mbe_pool["catalyst_score"]=mbe_pool.symbol.map(
        lambda s:catalyst_score(catmap.get(s,[]))
    )
    mbe_pool["catalyst_source"]=mbe_pool.symbol.map(
        lambda s:
        "NSE_PIT" if any(
            i.get("source_type")=="NSE_CORPORATE_ANNOUNCEMENT"
            for i in catmap.get(s,[])
        ) else "SCREENER_NON_PIT"
    )

    # Production recalculates early-inflection after catalyst evidence is attached.
    # Reproduce that step so early_watch/early_stage are based on the same fields.
    ei_final=score_early_inflection(mbe_pool,mbe_pool,catmap)
    for c in [
        "order_visibility","capacity_inflection","structural_theme",
        "early_inflection_score","early_stage"
    ]:
        if c in ei_final:
            mbe_pool[c]=ei_final[c].values

    mbe_pool["six_month_return"]=mbe_pool["ret_126d"]
    mbe_pool["entry_stage"]=mbe_pool["ret_126d"].apply(
        lambda x:"EARLY" if pd.notna(x) and x<=0.25 else
                  "EARLY_ACCELERATING" if pd.notna(x) and x<=0.50 else
                  "ESTABLISHED" if pd.notna(x) and x<=1.00 else "MATURE"
    )
    mbe_pool["early_watch"]=(
        mbe_pool.early_inflection_score.ge(65)&
        mbe_pool.fundamental_evidence.ge(.50)&
        mbe_pool.trap_firewall_pass
    )
    mbe_pool["production_mbe_gate"]=(
        mbe_pool.mbe_score.ge(65)&
        mbe_pool.fundamental_score.ge(60)&
        mbe_pool.fundamental_evidence.ge(.75)&
        mbe_pool.trap_firewall_pass&
        mbe_pool.pit_verified&
        mbe_pool.six_month_return.le(.50)
    )

    # Return every winner that was visible in the scanner, even if it never
    # reached a later pool. That distinction is central to falsifying the
    # "detected early" claim.
    rows=[]
    for sym,name in WINNERS.items():
        srow=target_screened[target_screened.symbol==sym]
        drow=target_discovery[target_discovery.symbol==sym]
        frow=fundamental_pool[fundamental_pool.symbol==sym]
        mrow=mbe_pool[mbe_pool.symbol==sym]
        base={}
        if not srow.empty:
            base.update(srow.iloc[0].to_dict())
        elif not drow.empty:
            base.update(drow.iloc[0].to_dict())
        base.update({
            "decision_date":as_of,
            "symbol":sym,
            "name":name,
            "screened":not srow.empty,
            "discovery_selected":not drow.empty,
            "fundamental_pool_selected":not frow.empty,
            "mbe_pool_selected":not mrow.empty,
        })
        if not drow.empty:
            base["discovery_rank"]=int(drow.iloc[0]["discovery_rank"])
        else:
            base["discovery_rank"]=None
        if not frow.empty:
            base["fundamental_rank"]=int(
                fundamental_pool.reset_index(drop=True).index[
                    fundamental_pool.reset_index(drop=True).symbol.eq(sym)
                ][0]+1
            )
        else:
            base["fundamental_rank"]=None
        if not mrow.empty:
            base.update(mrow.iloc[0].to_dict())
        else:
            for c in [
                "fundamental_score","fundamental_module_count",
                "fundamental_evidence","pit_verified","mbe_score",
                "mbe_stage","maturity_flag","maturity_penalty",
                "catalyst_score","catalyst_source","earnings_inflection",
                "operating_leverage","early_inflection_score","early_stage",
                "early_watch","production_mbe_gate","ret_21d","ret_63d",
                "ret_126d","ret_252d","close","entry_stage"
            ]:
                base.setdefault(c,None)
        rows.append({
            "decision_date":as_of,
            "symbol":sym,
            "name":name,
            "close":base.get("close"),
            "ret_21d":base.get("ret_21d"),
            "ret_63d":base.get("ret_63d"),
            "ret_126d":base.get("ret_126d"),
            "ret_252d":base.get("ret_252d"),
            "entry_stage":base.get("entry_stage"),
            "screened":base["screened"],
            "discovery_selected":base["discovery_selected"],
            "discovery_rank":base["discovery_rank"],
            "fundamental_pool_selected":base["fundamental_pool_selected"],
            "fundamental_rank":base["fundamental_rank"],
            "mbe_pool_selected":base["mbe_pool_selected"],
            "fundamental_score":base.get("fundamental_score"),
            "fundamental_module_count":base.get("fundamental_module_count"),
            "fundamental_evidence":base.get("fundamental_evidence"),
            "pit_verified":base.get("pit_verified"),
            "mbe_score":base.get("mbe_score"),
            "mbe_stage":base.get("mbe_stage"),
            "maturity_flag":base.get("maturity_flag"),
            "maturity_penalty":base.get("maturity_penalty"),
            "catalyst_score":base.get("catalyst_score"),
            "catalyst_source":base.get("catalyst_source"),
            "earnings_inflection":base.get("earnings_inflection"),
            "operating_leverage":base.get("operating_leverage"),
            "early_inflection_score":base.get("early_inflection_score"),
            "early_stage":base.get("early_stage"),
            "early_watch":base.get("early_watch"),
            "production_mbe_gate":base.get("production_mbe_gate"),
        })
    return rows

def _audit_rows_without_mbe(as_of,target_screened,target_discovery,discovery):
    rows=[]
    for sym,name in WINNERS.items():
        srow=target_screened[target_screened.symbol==sym]
        drow=target_discovery[target_discovery.symbol==sym]
        rows.append({
            "decision_date":as_of,"symbol":sym,"name":name,
            "close":float(srow.iloc[0].close) if not srow.empty else None,
            "ret_21d":float(srow.iloc[0].ret_21d) if not srow.empty else None,
            "ret_63d":float(srow.iloc[0].ret_63d) if not srow.empty else None,
            "ret_126d":float(srow.iloc[0].ret_126d) if not srow.empty else None,
            "ret_252d":float(srow.iloc[0].ret_252d) if not srow.empty else None,
            "entry_stage":None,
            "screened":not srow.empty,
            "discovery_selected":not drow.empty,
            "discovery_rank":int(drow.iloc[0].discovery_rank) if not drow.empty else None,
            "fundamental_pool_selected":False,
            "fundamental_rank":None,
            "mbe_pool_selected":False,
            "fundamental_score":None,"fundamental_module_count":None,
            "fundamental_evidence":None,"pit_verified":None,"mbe_score":None,
            "mbe_stage":None,"maturity_flag":None,"maturity_penalty":None,
            "catalyst_score":None,"catalyst_source":None,
            "earnings_inflection":None,"operating_leverage":None,
            "early_inflection_score":None,"early_stage":None,
            "early_watch":False,"production_mbe_gate":False,
        })
    return rows

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

if __name__=="__main__": main()    # FALLBACK DIAGNOSTIC: if a historical winner did not survive the production
    # scanner/fundamental/MBE pools, still reconstruct its latent MBE signal using
    # PIT fundamentals and full-market price percentiles. This does NOT promote the
    # name and is labelled diagnostic_only; it prevents the scanner funnel from
    # hiding whether the underlying MBE vectors existed before the run-up.
    all_targets=target_screened.copy()
    if not all_targets.empty:
        f_all=facts_df[facts_df.symbol.isin(all_targets.symbol)] if not facts_df.empty else pd.DataFrame()
        all_pit=set(f_all.symbol.astype(str).unique()) if not f_all.empty else set()
        missing_all=[s for s in all_targets.symbol.astype(str).tolist() if s not in all_pit]
        if missing_all:
            try:
                dyn=NSEPIT(); rr=[]
                for s in missing_all:
                    try: rr.extend(dyn.facts(s,as_of) or [])
                    except Exception: pass
                if rr:
                    ddf=pd.DataFrame(rr)
                    f_all=pd.concat([f_all,ddf],ignore_index=True) if not f_all.empty else ddf
                    all_pit.update(ddf.symbol.astype(str).unique())
            except Exception: pass
        rec_all=f_all.to_dict(orient="records") if not f_all.empty else []
        fs_all=score_fundamentals(all_targets,rec_all) if rec_all else pd.DataFrame()
        if not fs_all.empty:
            all_targets=all_targets.merge(fs_all,on="symbol",how="left")
            for cc in modules:
                if cc not in all_targets: all_targets[cc]=float("nan")
            all_targets["fundamental_score"]=all_targets[modules].mean(axis=1,skipna=True)
            all_targets["fundamental_module_count"]=all_targets[modules].notna().sum(axis=1)
            all_targets["pit_verified"]=all_targets.symbol.astype(str).isin(all_pit)

            # Map full-market percentile ranks by symbol, preserving PIT cross-section.
            px_idx=px.set_index("symbol")
            def rank_map(col,default=-1e9):
                s=pd.to_numeric(px_idx[col],errors="coerce").fillna(default)
                return s.rank(pct=True)*100
            r21m=rank_map("ret_21d"); r63m=rank_map("ret_63d"); r126m=rank_map("ret_126d")
            r252m=rank_map("ret_252d")
            all_targets["trend_score"]=(
                r63m.reindex(all_targets.symbol).fillna(0).values*.25+
                r126m.reindex(all_targets.symbol).fillna(0).values*.35+
                r252m.reindex(all_targets.symbol).fillna(0).values*.40
            )
            all_targets["discovery_score"]=all_targets["trend_score"]
            all_targets["early_momentum_score"]=(
                r21m.reindex(all_targets.symbol).fillna(0).values*.50+
                r63m.reindex(all_targets.symbol).fillna(0).values*.30+
                r126m.reindex(all_targets.symbol).fillna(0).values*.20
            )
            ei_all=score_early_inflection(all_targets,all_targets,{})
            for cc in ["earnings_inflection","operating_leverage","cash_inflection","balance_sheet_runway","early_inflection_score","early_stage"]:
                if cc in ei_all: all_targets[cc]=ei_all[cc].values
            all_targets["early_trend_component"]=(r21m.reindex(all_targets.symbol).fillna(0).values*.45+
                                                  r63m.reindex(all_targets.symbol).fillna(0).values*.35+
                                                  r126m.reindex(all_targets.symbol).fillna(0).values*.20)
            all_targets["acceleration_component"]=(
                (pd.to_numeric(px_idx["ret_21d"],errors="coerce").fillna(-1e9)-
                 pd.to_numeric(px_idx["ret_63d"],errors="coerce").fillna(-1e9)/3.0)
                .rank(pct=True)*100
            ).reindex(all_targets.symbol).fillna(0).values
            all_targets["maturity_flag"]=(
                (all_targets.ret_126d.fillna(0)>1.0)|(all_targets.ret_252d.fillna(0)>2.0)
            )
            all_targets["maturity_penalty"]=all_targets["maturity_flag"].astype(float)*15
            all_targets["earnings_component"]=all_targets["earnings_inflection"].fillna(50)
            all_targets["balance_component"]=all_targets["balance_sheet_runway"].fillna(50)
            all_targets["diagnostic_mbe_score"]=(
                all_targets.fundamental_score.fillna(0)*.45+
                all_targets.earnings_component*.20+
                all_targets.early_trend_component*.12+
                all_targets.acceleration_component*.08+
                all_targets.balance_component*.15-
                all_targets.maturity_penalty
            )
            all_targets["diagnostic_mbe_stage"]=all_targets.apply(
                lambda r:"MATURE_CONTINUATION" if bool(r.maturity_flag) else
                ("ACCELERATING" if float(r.acceleration_component)>=70 else "EARLY"),axis=1
            )
            all_targets["diagnostic_6m_return"]=all_targets["ret_126d"]
        else:
            all_targets=pd.DataFrame()

    # Return every winner with production status plus fallback diagnostic signal.

