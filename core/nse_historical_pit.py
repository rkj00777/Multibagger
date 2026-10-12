"""Historical point-in-time NSE results/XBRL ingestion.

Uses NSE's public corporates-financial-results endpoint, which carries filing
publication dates and XBRL URLs. This is intentionally separate from the live
integrated-filing adapter so changes to historical acquisition cannot break
the live scan. No current-value fallback is permitted.

Coverage is empirical, not assumed: the backfill report records which symbols
and decision dates actually yield eligible facts. NSE has purged some old XBRL
documents; missing documents remain missing and are never reconstructed from
today's financials.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

BASE = "https://www.nseindia.com"
FILINGS_API = BASE + "/api/corporates-financial-results"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Canonical names are the schema consumed by multibagger.modules.
ALIASES = {
    "revenue": ("RevenueFromOperations", "Revenue", "IncomeFromOperations",
                "Turnover", "InterestEarned"),
    "pat": ("ProfitLossForPeriod", "ProfitLossForThePeriod", "ProfitLoss",
            "ProfitAfterTax", "NetProfit", "ProfitLossFromOrdinaryActivitiesAfterTax"),
    "ebitda": ("EBITDA", "EarningsBeforeInterestTaxDepreciationAndAmortisation"),
    "cfo": ("CashFlowsFromUsedInOperatingActivities",
            "NetCashGeneratedFromOperatingActivities", "CashGeneratedFromOperations"),
    "capex": ("PurchaseOfPropertyPlantAndEquipment",
              "PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfTangibleAssets"),
    "debt": ("BorrowingsCurrentAndNonCurrent", "Borrowings", "Debt"),
    "cash": ("CashAndCashEquivalents", "CashAndBankBalances", "Cash"),
    "equity": ("EquityAttributableToOwnersOfParent", "Equity", "ShareholdersEquity"),
    "interest": ("FinanceCosts", "InterestExpense", "FinanceCost", "InterestExpended"),
    "eps": ("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "BasicEarningsLossPerShare", "BasicEarningsPerShare",
            "DilutedEarningsPerShare"),
}
FLOW_METRICS = {"revenue", "pat", "ebitda", "cfo", "capex", "interest", "eps"}
INSTANT_METRICS = {"debt", "cash", "equity"}


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _date(value):
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=None)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except Exception:
        return None


def _url(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return BASE + value
    return value if value.startswith("http") else None


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _filing_rows(payload, symbol):
    out, seen = [], set()
    for raw in _walk(payload):
        low = {str(k).lower(): v for k, v in raw.items()}
        xbrl = None
        for key in ("xbrl", "xbrlurl", "xbrl_url"):
            xbrl = _url(low.get(key))
            if xbrl:
                break
        filed = low.get("filingdate") or low.get("broadcastdate") or low.get("broadcast_date")
        if not xbrl or not filed:
            continue
        available = _date(filed)
        if not available:
            continue
        key = (xbrl, available.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "symbol": str(low.get("symbol") or symbol).upper(),
            "available_at": available.isoformat(),
            "xbrl_url": xbrl,
            "basis_hint": str(low.get("natureofreport") or low.get("consolidated") or "").lower(),
        })
    out.sort(key=lambda row: row["available_at"])
    return out


def _parse_xbrl(xml_bytes, filing):
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return []

    contexts = {}
    for node in root.findall(".//{*}context"):
        cid = node.attrib.get("id")
        if not cid:
            continue
        start = _date((node.find(".//{*}startDate").text
                       if node.find(".//{*}startDate") is not None else None))
        end = _date((node.find(".//{*}endDate").text
                     if node.find(".//{*}endDate") is not None else None))
        instant = _date((node.find(".//{*}instant").text
                         if node.find(".//{*}instant") is not None else None))
        has_dimension = node.find(".//{*}explicitMember") is not None or node.find(".//{*}typedMember") is not None
        contexts[cid] = {"start": start, "end": end, "instant": instant,
                         "dimensioned": has_dimension}

    tag_map = {}
    for metric, tags in ALIASES.items():
        for tag in tags:
            tag_map.setdefault(tag.lower(), metric)

    basis = "unknown"
    period_end_declared = None
    for node in root.iter():
        name = _local(node.tag).lower()
        value = (node.text or "").strip()
        if not value:
            continue
        if name == "natureofreportstandaloneconsolidated":
            basis = "consolidated" if "consolidated" in value.lower() else (
                "standalone" if "standalone" in value.lower() else "unknown")
        if name in {"dateofendofreportingperiod", "dateofendofreportingperiod"}:
            period_end_declared = _date(value)

    candidates = {metric: [] for metric in ALIASES}
    for node in root.iter():
        metric = tag_map.get(_local(node.tag).lower())
        if not metric or node.text is None:
            continue
        context = contexts.get(node.attrib.get("contextRef"))
        if not context or context["dimensioned"]:
            continue
        try:
            value = float(node.text.strip().replace(",", ""))
        except (ValueError, AttributeError):
            continue
        instant = context["instant"]
        start, end = context["start"], context["end"]
        period_end = instant or end
        if not period_end:
            continue
        # The filing's own declared end date wins. Otherwise select the latest
        # context end in the document; comparative periods cannot overwrite it.
        candidates[metric].append({
            "value": value, "period_end": period_end,
            "period_start": start, "instant": instant is not None,
            "duration_days": (end - start).days if start and end else None,
            "context_id": node.attrib.get("contextRef", ""),
        })

    if period_end_declared is None:
        all_ends = [v["period_end"] for values in candidates.values() for v in values]
        period_end_declared = max(all_ends) if all_ends else None
    if not period_end_declared:
        return []

    rows = []
    for metric, values in candidates.items():
        if not values:
            continue
        # Use only the reporting period, not prior-year comparatives. For flow
        # metrics prefer the shortest ordinary duration (quarter, not YTD);
        # for balance-sheet metrics prefer an instant context.
        values = [v for v in values if v["period_end"] == period_end_declared]
        if not values:
            continue
        if metric in FLOW_METRICS:
            durations = [v for v in values if v["duration_days"] is not None]
            if durations:
                quarter_like = [v for v in durations if 60 <= v["duration_days"] <= 130]
                chosen = min(quarter_like or durations,
                             key=lambda v: v["duration_days"])
            else:
                chosen = values[0]
        else:
            instants = [v for v in values if v["instant"]]
            chosen = sorted(instants, key=lambda v: (0 if v["context_id"].lower().startswith("one") else 1))[0] if instants else values[0]

        rows.append({
            "symbol": filing["symbol"], "metric": metric, "value": chosen["value"],
            "period_end": period_end_declared.date().isoformat(),
            "period_start": chosen["period_start"].date().isoformat() if chosen["period_start"] else None,
            "available_at": filing["available_at"], "basis": basis,
            "source_type": "NSE_CORPORATE_RESULTS_XBRL", "source_url": filing["xbrl_url"],
        })
    return rows


class NSEHistoricalPIT:
    """Rate-limited, cached adapter for historical NSE results filings."""

    def __init__(self, timeout=45, delay=1.2, retries=4, cache_dir="data/pit/cache"):
        self.timeout, self.delay, self.retries = timeout, delay, retries
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA, "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9", "Referer": BASE + "/",
        })
        self._last_request = 0.0
        self._catalog = {}
        self._facts = {}
        self._xbrl = {}
        try:
            self.session.get(BASE + "/", timeout=15)
        except Exception:
            pass

    def _get(self, url, params=None):
        last_error = None
        for attempt in range(self.retries):
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self._last_request = time.monotonic()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 200:
                    return response
                last_error = RuntimeError(f"HTTP {response.status_code}")
                if response.status_code not in (429, 502, 503, 504):
                    response.raise_for_status()
            except Exception as exc:
                last_error = exc
            if attempt < self.retries - 1:
                time.sleep((2, 5, 12)[min(attempt, 2)])
        raise RuntimeError(f"NSE request failed: {last_error}")

    def filings(self, symbol):
        symbol = symbol.upper()
        if symbol not in self._catalog:
            response = self._get(FILINGS_API, params={
                "index": "equities", "period": "Quarterly", "symbol": symbol,
            })
            payload = response.json()
            rows = _filing_rows(payload, symbol)
            self._catalog[symbol] = rows
        return self._catalog[symbol]

    def facts(self, symbol, as_of, max_filings=None):
        symbol = symbol.upper()
        cutoff = _date(as_of + " 23:59:59")
        rows = []
        for filing in self.filings(symbol):
            available = _date(filing["available_at"])
            if not available or available > cutoff:
                continue
            url = filing["xbrl_url"]
            if url not in self._xbrl:
                try:
                    response = self._get(url)
                    self._xbrl[url] = _parse_xbrl(response.content, filing)
                except Exception:
                    # Do not cache transport errors as permanently missing.
                    continue
            for fact in self._xbrl[url]:
                period_end = _date(fact.get("period_end"))
                fact_available = _date(fact.get("available_at"))
                if fact_available and fact_available <= cutoff and period_end and period_end <= cutoff:
                    rows.append(fact)
        # Prefer consolidated figures for each symbol/period/metric when they
        # exist, otherwise preserve standalone/unknown observations.
        if rows:
            keys = {(r["symbol"], r["metric"], r["period_end"], r.get("period_start"))
                    for r in rows if r.get("basis") == "consolidated"}
            rows = [r for r in rows if r.get("basis") == "consolidated" or
                    (r["symbol"], r["metric"], r["period_end"], r.get("period_start")) not in keys]
            dedup = {}
            for row in rows:
                key = (row["symbol"], row["metric"], row["period_end"], row.get("period_start"),
                       row["available_at"], row.get("basis"))
                dedup[key] = row
            rows = list(dedup.values())
        return rows
