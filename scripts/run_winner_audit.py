import json, os
import duckdb
import pandas as pd
from core.market_data import nse_cross_section
from core.nse_pit import NSEPIT
from core.pit_store import load_facts
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
    "BIRLACOT":"Birla Cotsyn (India)",
    "JTLDEFENCE":"JTL Defence",
    "KETOMOTORS":"Keto Motors",
    "AHLWEST":"Asian Hotels (West)",
    "GBLINFRA":"Global Infratech & Finance",
    "MRUGESH":"Mrugesh Trading",
    "RAYMOND":"Raymond",
    "TBZ":"Tribhovandas Bhimji Zaveri",
    "MOREPENLAB":"Morepen Laboratories",
    "SARAUTO":"SAR Auto Products",
    "KABRAEXTRU":"Kabra Extrusiontechnik",
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


FUNNEL_DIAGNOSTICS = []

def build_historical_funnel(px, as_of):
    """Build a leakage-safe historical discovery union using only data available at as_of.

    Current Chartink/Screener results are deliberately excluded from historical
    backtests because they contain present-day membership and would leak future
    information. Their live counterparts remain discovery-only inputs in run_live.
    """
    x = px.copy()
    for col in ["ret_21d", "ret_63d", "ret_126d", "ret_252d",
                "avg_turnover_60d", "pct_off_high"]:
        x[col] = pd.to_numeric(x.get(col), errors="coerce")
    liquid = x[x["avg_turnover_60d"].ge(2_000_000) & x["ret_63d"].notna()].copy()
    if liquid.empty:
        return liquid, {"as_of": as_of, "universe": len(px), "eligible": 0,
                        "technical_arm": 0, "pit_fundamental_arm": 0,
                        "near_high_arm": 0, "union": 0, "pit_facts_symbols": 0}

    def pct(s):
        return s.rank(pct=True).fillna(0) * 100

    # Broad trend-transition arm; intentionally does not require a 52-week high.
    liquid["historical_technical_score"] = (
        pct(liquid["ret_21d"].fillna(-1e9)) * .40
        + pct(liquid["ret_63d"].fillna(-1e9)) * .30
        + pct(liquid["ret_126d"].fillna(-1e9)) * .15
        + pct(liquid["avg_turnover_60d"].fillna(0)) * .15
    )
    eligible_n = len(liquid)
    tech_n = min(500, max(100, int(eligible_n * .25)))
    technical = liquid.nlargest(min(tech_n, eligible_n), "historical_technical_score")

    # Reconstruct a fundamental discovery arm from the PIT store at the decision
    # date. Current Screener data is not used for historical evaluation.
    facts_df = load_facts(as_of)
    pit_symbols = int(facts_df["symbol"].nunique()) if not facts_df.empty else 0
    fundamental = pd.DataFrame()
    if not facts_df.empty:
        fs_all = score_fundamentals(liquid, facts_df.to_dict(orient="records"))
        if not fs_all.empty:
            mods = ["valuation_gap", "earnings_acceleration", "cash_conversion",
                    "reinvestment_roic", "governance_balance_sheet"]
            for col in mods:
                if col not in fs_all:
                    fs_all[col] = float("nan")
            fs_all["historical_fundamental_score"] = fs_all[mods].mean(axis=1, skipna=True)
            fs_all["historical_module_count"] = fs_all[mods].notna().sum(axis=1)
            fs_all["historical_pit_fundamental_hit"] = (
                fs_all["historical_module_count"].ge(3)
                & pd.to_numeric(fs_all["fundamental_evidence"], errors="coerce").ge(.45)
            )
            fundamental = fs_all[fs_all["historical_pit_fundamental_hit"]].copy()
            if not fundamental.empty:
                fund_n = min(200, max(30, int(len(fundamental) * .20)))
                fundamental = fundamental.nlargest(
                    min(fund_n, len(fundamental)), "historical_fundamental_score"
                )

    # Optional constructive-near-high arm is capped and kept independent; it is
    # not a mandatory condition for the other two arms.
    near = liquid[
        liquid["pct_off_high"].ge(-.15)
        & liquid["ret_21d"].gt(0)
        & liquid["ret_126d"].le(.50)
    ].copy()
    near_n = min(150, max(30, int(eligible_n * .10)))
    near = near.nlargest(min(near_n, len(near)), "historical_technical_score")

    tech_syms = set(technical["symbol"].astype(str))
    fund_syms = set(fundamental["symbol"].astype(str)) if not fundamental.empty else set()
    near_syms = set(near["symbol"].astype(str))
    union_syms = tech_syms | fund_syms | near_syms
    screened = liquid[liquid["symbol"].astype(str).isin(union_syms)].copy()
    screened["historical_technical_arm"] = screened["symbol"].astype(str).isin(tech_syms)
    screened["historical_pit_fundamental_arm"] = screened["symbol"].astype(str).isin(fund_syms)
    screened["historical_near_high_arm"] = screened["symbol"].astype(str).isin(near_syms)
    screened["scanner_score"] = (
        screened["historical_technical_arm"].astype(float) * 45
        + screened["historical_pit_fundamental_arm"].astype(float) * 40
        + screened["historical_near_high_arm"].astype(float) * 15
    )
    diagnostics = {
        "as_of": as_of, "universe": int(len(px)), "eligible": int(eligible_n),
        "technical_arm": int(len(tech_syms)), "pit_fundamental_arm": int(len(fund_syms)),
        "near_high_arm": int(len(near_syms)), "union": int(len(union_syms)),
        "pit_facts_symbols": pit_symbols,
        "method": "historical PIT price/fundamental proxies; no live Chartink/Screener membership"
    }
    return screened, diagnostics


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

    screened, funnel_diag = build_historical_funnel(px, as_of)
    FUNNEL_DIAGNOSTICS.append(funnel_diag)
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

    # Current announcement endpoints cannot reconstruct archived historical
    # catalyst evidence. Do not query live NSE/Screener pages in this PIT audit.
    # Missing archived catalyst evidence is explicitly UNVERIFIED, not a score of 50.
    catmap = {}
    mbe_pool["catalyst_score"] = float("nan")
    mbe_pool["catalyst_source"] = "NO_HISTORICAL_PIT_ARCHIVE"

    # Early-stage diagnostics are provisional without archived PIT catalyst evidence.
    ei_final = score_early_inflection(mbe_pool, mbe_pool, catmap)
    for c in ["order_visibility", "capacity_inflection", "structural_theme",
              "early_inflection_score", "early_stage"]:
        if c in ei_final:
            mbe_pool[c] = ei_final[c].values

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
        mbe_pool.catalyst_score.ge(60)&
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
        if mrow.empty:
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

def summarize_historical_detection(winners, rows):
    """Summarize pre-run detection without confusing retrospective labels with model inputs."""
    outcomes = {str(w.get("symbol")): w for w in winners}
    by_symbol = {}
    for row in rows:
        by_symbol.setdefault(str(row.get("symbol")), []).append(row)

    stages = [
        ("screened", "Reached historical discovery union"),
        ("discovery_selected", "Entered discovery pool"),
        ("fundamental_pool_selected", "Entered PIT fundamental pool"),
        ("mbe_pool_selected", "Entered MBE pool"),
        ("production_mbe_gate", "Passed every production gate"),
    ]
    per_symbol = []
    for symbol, outcome in outcomes.items():
        hit_date = outcome.get("first_plus_100_date")
        hit_date = hit_date if isinstance(hit_date, str) and hit_date not in ("nan", "NaT", "") else None
        checkpoints = sorted(by_symbol.get(symbol, []), key=lambda r: r.get("decision_date") or "")
        pre_run = [
            r for r in checkpoints
            if hit_date is None or (r.get("decision_date") and r["decision_date"] < hit_date)
        ]
        ret = outcome.get("six_month_return")
        try:
            return_6m = float(ret)
        except (TypeError, ValueError):
            return_6m = float("nan")
        realized_2x = bool(pd.notna(return_6m) and return_6m >= 1.0)
        result = {
            "symbol": symbol,
            "name": outcome.get("name"),
            "six_month_return": return_6m if pd.notna(return_6m) else None,
            "realized_2x_in_window": realized_2x,
            "first_plus_100_date": hit_date,
            "pre_run_checkpoints": len(pre_run),
        }
        for key, _label in stages:
            hit_rows = [r for r in pre_run if bool(r.get(key))]
            result[key + "_before_plus_100"] = bool(hit_rows)
            result[key + "_first_date"] = hit_rows[0].get("decision_date") if hit_rows else None
        per_symbol.append(result)

    cohort_2x = [x for x in per_symbol if x["realized_2x_in_window"]]
    cohort_other = [x for x in per_symbol if not x["realized_2x_in_window"]]
    rates = {}
    for key, label in stages:
        detected = sum(x[key + "_before_plus_100"] for x in cohort_2x)
        rates[key] = {
            "label": label,
            "detected_2x_names": int(detected),
            "eligible_2x_names": int(len(cohort_2x)),
            "pre_run_recall": float(detected / len(cohort_2x)) if cohort_2x else None,
            "detected_other_names": int(sum(x[key + "_before_plus_100"] for x in cohort_other)),
            "other_names": int(len(cohort_other)),
        }

    return {
        "definition": "A realized 2x name has >=100% close-to-close return from the first available checkpoint price through 2026-08-07. Detection must occur on a checkpoint strictly before its first +100% date.",
        "sample_size": len(per_symbol),
        "realized_2x_names": len(cohort_2x),
        "other_or_failed_names": len(cohort_other),
        "stage_recall": rates,
        "per_symbol": per_symbol,
        "limitations": [
            "This is a small, retrospectively assembled six-month falsification cohort, not a representative out-of-sample estimate.",
            "Historical catalyst evidence is unavailable, so no historical promotion can be certified.",
            "Price/fundamental discovery arms are reconstructed from available PIT proxies, not historical Chartink/Screener membership.",
        ],
    }


def main():
    rows=[]
    for d in CHECKPOINTS: rows.extend(audit_date(d))
    th=threshold_dates()
    detection = summarize_historical_detection(th.to_dict(orient="records"), rows)
    os.makedirs("reports",exist_ok=True)
    out={"status":"WINNER_MBE_PIT_AUDIT_COMPLETE",
         "winner_window":"2026-02-06 to 2026-08-07",
         "method":"Production MBE score reproduced at PIT checkpoints; no post-checkpoint fundamentals used",
         "note":"A stock is considered 'detected before discovery' only if its production MBE score/gates clear at a checkpoint preceding its major run-up. This audit does not use the later winner outcome in the score.",
         "winners":th.to_dict(orient="records"),
         "historical_detection_metrics":detection,
         "checkpoint_signals":rows,
         "historical_funnel_diagnostics":FUNNEL_DIAGNOSTICS,
         "leakage_guard":"Historical audit does not query current Chartink/Screener screens or live announcement pages; current scanner outputs are reserved for live discovery.",
         "catalyst_validation_status":"UNVERIFIED_NO_HISTORICAL_PIT_ARCHIVE",
         "production_readiness":"NOT_VALIDATED_FOR_AUTOMATED_CAPITAL_DEPLOYMENT",
         "production_gate_note":"Historical promotion is not credited without archived PIT catalyst evidence; this is a data limitation, not proof that a company lacked a catalyst."}
    json.dump(out,open("reports/multibagger-winner-audit.json","w"),indent=2,default=str)
    print(json.dumps(out,indent=2,default=str))

if __name__=="__main__": main()
