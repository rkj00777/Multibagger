"""External first-pass scanners used only for candidate funneling.

Chartink = technical transition / momentum discovery.
Screener = current public fundamental-growth discovery.
Neither source is a PIT authority. PIT facts remain authoritative for promotion.
Both scanners are best-effort and have deterministic local fallbacks.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, Set

import pandas as pd
import requests
from bs4 import BeautifulSoup

CHARTINK_URL = "https://chartink.com/screener/process"
CHARTINK_PAGE = "https://chartink.com/screener/"
SCREENER_SCREENS = [
    "https://www.screener.in/screens/2208896/export-screen/",
]

CHARTINK_CLAUSES = {
    "trend_transition": """( {cash} ( latest close > latest sma( latest close , 20 ) and latest sma( latest close , 20 ) > latest sma( latest close , 50 ) and latest close > 1 day ago close and latest volume > latest sma( volume , 20 ) * 1.2 and latest rsi( 14 ) > 50 and latest rsi( 14 ) < 75 ) )""",
    "breakout_volume": """( {cash} ( latest close > latest max( 20 , latest high ) and latest volume > latest sma( volume , 20 ) * 1.5 ) )""",
    "strength_confirmation": """( {cash} ( latest close > latest sma( latest close , 50 ) and latest sma( latest close , 50 ) > latest sma( latest close , 100 ) and latest rsi( 14 ) > 55 and latest rsi( 14 ) < 80 ) )""",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; MultibaggerScanner/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/json",
    })
    return s


def run_chartink_clause(clause: str, timeout: int = 25) -> Set[str]:
    """Run one public Chartink scan. Returns an empty set on access/CSRF failure."""
    try:
        with _session() as s:
            page = s.get(CHARTINK_PAGE, timeout=timeout)
            soup = BeautifulSoup(page.text, "html.parser")
            token = soup.select_one('meta[name="csrf-token"]')
            if not token or not token.get("content"):
                return set()
            r = s.post(
                CHARTINK_URL,
                headers={
                    "X-CSRF-TOKEN": token["content"],
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": CHARTINK_PAGE,
                },
                data={"scan_clause": clause},
                timeout=timeout,
            )
            if not r.ok:
                return set()
            data = r.json().get("data", [])
            return {
                str(row.get("nsecode", "")).strip().upper()
                for row in data
                if str(row.get("nsecode", "")).strip()
            }
    except Exception:
        return set()


def chartink_scan() -> Dict[str, Set[str]]:
    out = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(run_chartink_clause, clause): name for name, clause in CHARTINK_CLAUSES.items()}
        for f in as_completed(futs):
            try:
                out[futs[f]] = f.result()
            except Exception:
                out[futs[f]] = set()
    return out


def _screener_symbols_from_page(html: str) -> Set[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: Set[str] = set()
    for a in soup.select('a[href*="/company/"]'):
        href = a.get("href", "")
        m = re.search(r"/company/([^/?#]+)/", href)
        if m:
            out.add(m.group(1).upper())
    return out


def screener_scan(max_pages: int = 12, timeout: int = 20) -> Set[str]:
    """Read a public Screener growth screen. No login/API is assumed."""
    out: Set[str] = set()
    for base in SCREENER_SCREENS:
        for page in range(1, max_pages + 1):
            url = base if page == 1 else base + f"?page={page}"
            try:
                with _session() as s:
                    r = s.get(url, timeout=timeout)
                    if not r.ok:
                        break
                    got = _screener_symbols_from_page(r.text)
                    if not got:
                        break
                    before = len(out)
                    out.update(got)
                    if len(out) == before and page > 1:
                        break
            except Exception:
                break
    return out


def local_technical_scan(df: pd.DataFrame) -> Set[str]:
    """Fallback when Chartink is unreachable; mirrors the intent, not the source."""
    x = df.copy()
    for c in ["close", "ret_21d", "ret_63d", "avg_turnover_60d"]:
        x[c] = pd.to_numeric(x.get(c), errors="coerce")
    cond = (
        x["avg_turnover_60d"].ge(2_000_000)
        & x["ret_21d"].notna()
        & x["ret_63d"].notna()
        & x["ret_21d"].gt(0)
        & x["ret_63d"].gt(-0.05)
    )
    return set(x.loc[cond, "symbol"].astype(str).str.upper())


def build_scanner_funnel(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Return cross-section with scanner flags and a confluence score."""
    chart = chartink_scan()
    chart_union = set().union(*chart.values()) if chart else set()
    screener = screener_scan()

    local_fallback = False
    if not chart_union:
        chart_union = local_technical_scan(df)
        local_fallback = True

    x = df.copy()
    x["chartink_hit"] = x["symbol"].astype(str).str.upper().isin(chart_union)
    x["screener_hit"] = x["symbol"].astype(str).str.upper().isin(screener)

    # Scanner confluence is deliberately a funnel signal, not a promotion score.
    x["scanner_score"] = (
        x["chartink_hit"].astype(float) * 50
        + x["screener_hit"].astype(float) * 35
        + pd.to_numeric(x["ret_21d"], errors="coerce").rank(pct=True).fillna(0) * 15
    )

    # Keep the union, then let the MBE engine perform the deeper ranking.
    mask = x["chartink_hit"] | x["screener_hit"]
    screened = x[mask].copy()
    if screened.empty:
        screened = x.copy()
    screened = screened.sort_values(
        ["scanner_score", "avg_turnover_60d"], ascending=False
    )

    status = {
        "chartink_scans": {k: len(v) for k, v in chart.items()},
        "chartink_union": len(chart_union),
        "screener_symbols": len(screener),
        "chartink_local_fallback": local_fallback,
        "scanner_union": int(mask.sum()),
        "scanner_funnel": "CHARTINK_TECHNICAL_PLUS_SCREENER_FUNDAMENTAL",
    }
    return screened, status
