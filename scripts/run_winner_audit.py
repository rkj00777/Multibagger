import json, os
import duckdb
import pandas as pd
from core.market_data import nse_cross_section
from core.nse_fundamentals import NSEFundamentals
from core.catalyst import NSECatalyst, ScreenerCatalyst, catalyst_score
from multibagger.modules import score_fundamentals
from multibagger.early_inflection import score_early_inflection
from multibagger.firewall import apply_trap_firewall

WINNERS = {
    "STLTECH":"Sterlite Technologies",
    "HFCL":"HFCL",
    "WELCORP":"Welspun Corp",
    "CUPID":"Cupid",
    "SIGMAADV":"Sigma Advanced Systems",
    "YASHO":"Yasho Industries",
    "DIACABS":"Diamond Power Infrastructure",
    "INDOTECH":"Indo Tech Transformers",
    "BLISSGVS":"Bliss GVS Pharma",
}
CHECKPOINTS = ["2026-02-06","2026-03-06","2026-04-06","2026-05-06"]

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
        out.append({"symbol":s,"name":n,"start_price":p0,
                    "end_date":"2026-08-07",
                    "six_month_return":float(g[g.date<=pd.Timestamp("2026-08-07")].iloc[-1].close/p0-1) if not g[g.date<=pd.Timestamp("2026-08-07")].empty else None,
                    "first_plus_100_date":str(hit.iloc[0].date.date()) if not hit.empty else None})
    return pd.DataFrame(out)

def audit_date(as_of):
    px=nse_cross_section(as_of)
    if px.empty:return []
    for c in ["ret_63d","ret_126d","ret_252d","avg_turnover_60d"]:
        px[c]=pd.to_numeric(px[c],errors="coerce")
    rank63=px.ret_63d.fillna(-1e9).rank(pct=True)*100
    rank126=px.ret_126d.fillna(-1e9).rank(pct=True)*100
    rank252=px.ret_252d.fillna(-1e9).rank(pct=True)*100
    px["trend_score"]=rank63*.25+rank126*.35+rank252*.40
    target=px[px.symbol.isin(WINNERS)].copy()
    if target.empty:return []
    facts=NSEFundamentals(workers=6).batch(target.symbol.tolist(),as_of)
    fs=score_fundamentals(target,facts)
    if fs.empty:return [{"decision_date":as_of,"symbol":s,"name":WINNERS[s],"status":"NO_PIT_FACTS"} for s in target.symbol]
    cats=NSECatalyst(workers=6).batch(target.symbol.tolist(),as_of)
    source="NSE"
    if not cats:
        cats=ScreenerCatalyst(workers=3).batch(target.symbol.tolist(),as_of); source="SCREENER_NON_PIT"
    catmap={}
    for x in cats:
        sym=x.get("symbol") or x.get("sym")
        if sym:catmap.setdefault(sym,[]).append(x)
    z=target.merge(fs,on="symbol",how="left")
    z["catalyst_score"]=z.symbol.map(lambda s:catalyst_score(catmap.get(s,[])))
    z["fundamental_score"]=z[["valuation_gap","earnings_acceleration","cash_conversion","reinvestment_roic","governance_balance_sheet"]].mean(axis=1,skipna=False)
    ei=score_early_inflection(z,z,catmap)
    for c in ["earnings_inflection","operating_leverage","order_visibility","capacity_inflection","structural_theme","early_inflection_score","early_stage"]:
        z[c]=ei[c].values
    z=apply_trap_firewall(z)
    z["early_watch"]=z.early_inflection_score.ge(65)&z.fundamental_evidence.ge(.50)&z.trap_firewall_pass
    z["promoted"]=z.early_inflection_score.ge(70)&z.fundamental_score.ge(65)&z.catalyst_score.ge(55)&z.fundamental_evidence.ge(.75)&z.trap_firewall_pass&z.trend_score.ge(45)
    z["decision_date"]=as_of;z["name"]=z.symbol.map(WINNERS);z["catalyst_source"]=source
    return z[["decision_date","symbol","name","trend_score","fundamental_score","fundamental_evidence","catalyst_score","earnings_inflection","operating_leverage","order_visibility","capacity_inflection","structural_theme","early_inflection_score","early_stage","early_watch","promoted"]].to_dict("records")

def main():
    rows=[]
    for d in CHECKPOINTS: rows.extend(audit_date(d))
    th=threshold_dates()
    os.makedirs("reports",exist_ok=True)
    out={"status":"WINNER_PIT_AUDIT_COMPLETE","winner_window":"2026-02-06 to 2026-08-07","method":"PIT checkpoints; no post-checkpoint fundamentals used","winners":th.to_dict(orient="records"),"checkpoint_signals":rows}
    json.dump(out,open("reports/multibagger-winner-audit.json","w"),indent=2,default=str)
    print(json.dumps(out,indent=2,default=str))

if __name__=="__main__": main()
