import pandas as pd
import math

THEME_TERMS = {
    "ai_datacenter": ["ai", "data centre", "data center", "hyperscaler", "gpu", "fibre", "fiber"],
    "defence": ["defence", "defense", "anti-drone", "drone", "missile", "aerospace"],
    "power_infra": ["transmission", "transformer", "grid", "power infrastructure", "substation"],
    "energy_infra": ["lng", "pipeline", "oil", "gas", "hydrogen", "aramco"],
    "capacity": ["capacity", "expansion", "capex", "new plant", "commission"],
    "orders": ["order book", "orderbook", "order win", "order intake", "contract", "award"],
    "long_term": ["long-term", "long term", "multi-year", "15-year", "10-year"]
}

def _clip(x, lo=0.0, hi=100.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return float("nan")

def _growth(cur, prev):
    try:
        if pd.notna(cur) and pd.notna(prev) and float(prev) != 0:
            return float(cur) / abs(float(prev)) - 1.0
    except Exception:
        pass
    return float("nan")

def _growth_score(g):
    if pd.isna(g):
        return float("nan")
    # 0% growth=50; +20%=70; +50%=100; negative growth approaches 0.
    return _clip(50 + 100 * float(g))

def _theme_score(items):
    text = " ".join(str(x).lower() for x in items)
    hits = {k: sum(text.count(t) for t in terms) for k, terms in THEME_TERMS.items()}
    active = sum(1 for v in hits.values() if v > 0)
    weighted = sum(min(v, 3) for v in hits.values())
    return _clip(35 + active * 8 + weighted * 4)

def _order_score(items):
    text = " ".join(str(x).lower() for x in items)
    hits = sum(text.count(t) for t in THEME_TERMS["orders"])
    return _clip(40 + min(hits, 6) * 10)

def _capacity_score(items):
    text = " ".join(str(x).lower() for x in items)
    hits = sum(text.count(t) for t in THEME_TERMS["capacity"])
    return _clip(40 + min(hits, 6) * 10)

def score_early_inflection(price_df, fundamental_df, catalyst_map):
    if fundamental_df is None or fundamental_df.empty:
        base = price_df[["symbol", "trend_score", "discovery_score"]].copy()
        base["earnings_inflection"] = float("nan")
        base["operating_leverage"] = float("nan")
        base["cash_inflection"] = float("nan")
        base["balance_sheet_runway"] = float("nan")
        base["order_visibility"] = base.symbol.map(lambda s: _order_score(catalyst_map.get(s, [])))
        base["capacity_inflection"] = base.symbol.map(lambda s: _capacity_score(catalyst_map.get(s, [])))
        base["structural_theme"] = base.symbol.map(lambda s: _theme_score(catalyst_map.get(s, [])))
        base["early_inflection_score"] = (
            base["trend_score"].fillna(0) * .15 +
            base["order_visibility"].fillna(0) * .20 +
            base["capacity_inflection"].fillna(0) * .15 +
            base["structural_theme"].fillna(0) * .20
        )
        return base

    out = fundamental_df.copy()
    for c in ["revenue_growth", "pat_growth", "ebitda_growth", "operating_leverage", "cash_conversion_change"]:
        if c not in out:
            out[c] = float("nan")

    out["earnings_inflection"] = (
        out["pat_growth"].map(_growth_score) * .55 +
        out["revenue_growth"].map(_growth_score) * .25 +
        out["ebitda_growth"].map(_growth_score) * .20
    )
    out["operating_leverage"] = (
        (out["pat_growth"] - out["revenue_growth"]).map(lambda x: _clip(50 + 100*x) if pd.notna(x) else float("nan"))
    )
    out["cash_inflection"] = out["cash_conversion_change"].map(lambda x: _clip(50 + 100*x) if pd.notna(x) else float("nan"))
    out["balance_sheet_runway"] = out["governance_balance_sheet"]

    out["order_visibility"] = out.symbol.map(lambda s: _order_score(catalyst_map.get(s, [])))
    out["capacity_inflection"] = out.symbol.map(lambda s: _capacity_score(catalyst_map.get(s, [])))
    out["structural_theme"] = out.symbol.map(lambda s: _theme_score(catalyst_map.get(s, [])))

    out["early_inflection_score"] = (
        out["earnings_inflection"].fillna(50) * .25 +
        out["order_visibility"].fillna(40) * .15 +
        out["structural_theme"].fillna(40) * .15 +
        out["operating_leverage"].fillna(50) * .10 +
        out["capacity_inflection"].fillna(40) * .10 +
        out["cash_inflection"].fillna(50) * .075 +
        out["balance_sheet_runway"].fillna(25) * .075 +
        out["trend_score"].fillna(0) * .10
    )
    out["early_stage"] = pd.cut(
        out["early_inflection_score"],
        bins=[-math.inf, 50, 65, 75, 85, math.inf],
        labels=["NO_SIGNAL", "EMERGING", "EARLY_WATCH", "HIGH_POTENTIAL", "EXCEPTIONAL"]
    ).astype(str)
    return out
