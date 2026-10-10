"""Exchange-first PIT ingestion and normalized snapshot storage.

Primary source: NSE integrated financial/XBRL filings.
No web scraping and no current-value substitution for PIT observations.
"""

from __future__ import annotations

import json, os, time, hashlib
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests

BASE = "https://www.nseindia.com"
API = BASE + "/api/integrated-filing-results"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "Chrome/131 Safari/537.36")

ALIASES = {
    "revenue": ["RevenueFromOperations", "Revenue", "IncomeFromOperations", "Turnover"],
    "pat": ["ProfitLoss", "ProfitForThePeriod", "ProfitAfterTax", "NetProfit"],
    "ebit": ["EBIT", "EarningsBeforeInterestAndTax", "OperatingProfit"],
    "ebitda": ["EBITDA", "EarningsBeforeInterestTaxDepreciationAndAmortisation"],
    "cfo": ["CashFlowsFromUsedInOperatingActivities", "NetCashGeneratedFromOperatingActivities", "CashGeneratedFromOperations"],
    "capex": ["PurchaseOfPropertyPlantAndEquipment", "PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfTangibleAssets"],
    "debt": ["Borrowings", "Debt", "BorrowingsCurrentAndNonCurrent"],
    "cash": ["CashAndCashEquivalents", "CashAndBankBalances", "Cash"],
    "equity": ["Equity", "EquityAttributableToOwnersOfParent", "ShareholdersEquity"],
    "interest": ["FinanceCosts", "InterestExpense", "FinanceCost"],
    "shares": ["NumberOfSharesOutstanding", "EquitySharesOutstanding"],
    "eps": ["BasicEarningsLossPerShare", "BasicEarningsPerShare", "DilutedEarningsPerShare"],
}

def _dt(v):
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    for f in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).replace(tzinfo=None)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except Exception:
        return None

def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None

def _url(v):
    if not isinstance(v, str):
        return None
    v = v.strip()
    if v.startswith("//"): return "https:" + v
    if v.startswith("/"): return BASE + v
    return v if v.startswith("http") else None

def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from _walk(v)
    elif isinstance(o, list):
        for v in o: yield from _walk(v)

def _catalog_rows(payload):
    out = []
    for d in _walk(payload):
        low = {str(k).lower(): v for k, v in d.items()}
        # Current NSE Integrated Filing schema:
        # symbol, smName/cmName, qe_Date, type_Sub, audited,
        # consolidated, broadcast_Date, creation_Date, xbrl, ixbrl.
        xurl = _url(low.get("xbrl"))
        if not xurl:
            # tolerate minor schema variants
            for k, v in low.items():
                if "xbrl" in k and k != "ixbrl":
                    xurl = _url(v)
                    if xurl: break
        symbol = low.get("symbol") or low.get("sym")
        isin = low.get("isin") or low.get("sm_isin") or low.get("isinno")
        period = low.get("qe_date") or low.get("qeDate") or low.get("period_end")
        broadcast = low.get("broadcast_date") or low.get("broadcastdate")
        creation = low.get("creation_date") or low.get("creationdate")
        avail = _dt(creation) or _dt(broadcast)
        if xurl and (symbol or isin) and period:
            out.append({
                "symbol": str(symbol).upper().strip() if symbol else None,
                "isin": isin,
                "period_end": _dt(period).date().isoformat() if _dt(period) else None,
                "available_at": avail.isoformat() if avail else None,
                "xbrl_url": xurl,
                "raw": d,
            })
    ded = {}
    for r in out:
        ded[(r["symbol"], r["xbrl_url"])] = r
    return list(ded.values())

def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

def _contexts(root):
    out = {}
    for x in root.findall(".//{*}context"):
        i = x.attrib.get("id")
        if not i: continue
        a = x.find(".//{*}instant"); s = x.find(".//{*}startDate"); e = x.find(".//{*}endDate")
        out[i] = {
            "instant": _dt(a.text if a is not None else None),
            "start": _dt(s.text if s is not None else None),
            "end": _dt(e.text if e is not None else None),
        }
    return out

def _metric(tag):
    t = _local(tag).lower()
    for metric, aliases in ALIASES.items():
        if any(a.lower() in t for a in aliases):
            return metric
    return None

def _parse_xbrl(raw, filing):
    import xml.etree.ElementTree as ET
    import re, html
    from bs4 import BeautifulSoup
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    text = re.sub(r"&([A-Za-z][A-Za-z0-9]+);",
                  lambda m: html.entities.html5.get(m.group(1)+";", m.group(0)), text)
    try:
        root = ET.fromstring(text)
    except Exception:
        soup = BeautifulSoup(text, "lxml-xml")
        root = ET.fromstring(str(soup).encode())
    ctx = _contexts(root)
    rows = []
    for f in root.iter():
        metric = _metric(f.tag)
        value = _num(f.text)
        if not metric or value is None: continue
        c = ctx.get(f.attrib.get("contextRef"), {})
        period_end = c.get("end") or c.get("instant")
        if not period_end: continue
        rows.append({
            "symbol": filing.get("symbol"),
            "isin": filing.get("isin"),
            "metric": metric,
            "value": value,
            "period_end": period_end.date().isoformat(),
            "period_start": c.get("start").date().isoformat() if c.get("start") else None,
            "duration_days": (c["end"]-c["start"]).days if c.get("end") and c.get("start") else 0,
            "available_at": filing.get("available_at"),
            "source_type": "NSE_XBRL",
            "source_url": filing.get("xbrl_url"),
        })
    ded = {(r["symbol"], r["metric"], r["period_end"], r["period_start"]): r for r in rows}
    return list(ded.values())

class NSEPIT:
    def __init__(self, timeout=25, retries=4):
        self.timeout, self.retries = timeout, retries
        self.s = requests.Session()
        # Cache filings and parsed XBRL per client so walk-forward validation
        # does not refetch the same company filings at every decision date.
        self._catalog_cache = {}
        self._catalog_pages = {}
        self._catalog_complete = set()
        self._xbrl_cache = {}
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": BASE + "/",
            "Connection": "keep-alive",
        })
        try:
            self.s.get(BASE + "/", timeout=15)
        except Exception:
            pass

    def _get(self, url, **kwargs):
        last = None
        for i in range(self.retries):
            try:
                r = self.s.get(url, timeout=self.timeout, **kwargs)
                if r.status_code in (429, 502, 503, 504):
                    time.sleep(min(2 ** i, 12)); continue
                r.raise_for_status()
                return r
            except Exception as e:
                last = e
                time.sleep(min(2 ** i, 12))
        if last: raise last
        raise RuntimeError("NSE request failed")

    def catalog(self, symbol, as_of, page_size=100):
        """Return filings available by as_of, paging backward when necessary.

        NSE returns the newest filings first. A historical cutoff may therefore
        require more than the first page. Cache raw rows across dates and fetch
        enough pages to obtain at least eight eligible filings for this cutoff.
        """
        key = (str(symbol).upper(), int(page_size))
        cached = self._catalog_cache.setdefault(key, [])
        cutoff = _dt(as_of + " 23:59:59")

        def eligible(rows):
            return [
                x for x in rows
                if x.get("available_at")
                and _dt(x["available_at"])
                and _dt(x["available_at"]) <= cutoff
                and x.get("period_end")
                and _dt(x["period_end"])
                and _dt(x["period_end"]) <= cutoff
            ]

        max_pages = 20
        while (
            len(eligible(cached)) < 8
            and key not in self._catalog_complete
            and self._catalog_pages.get(key, 0) < max_pages
        ):
            page = self._catalog_pages.get(key, 0) + 1
            params = {
                "type": "Integrated Filing- Financials",
                "page": page,
                "size": page_size,
                "index": "equities",
                "period_ended": "all",
                "symbol": symbol,
            }
            try:
                payload = self._get(API, params=params).json()
                batch = _catalog_rows(payload)
            except Exception:
                # Preserve any usable cached rows; do not turn an acquisition
                # outage into fabricated or current-value fallback data.
                self._catalog_complete.add(key)
                break

            known = {x.get("xbrl_url") for x in cached}
            for row in batch:
                if row.get("xbrl_url") not in known:
                    cached.append(row)
                    known.add(row.get("xbrl_url"))
            self._catalog_pages[key] = page
            if not batch:
                self._catalog_complete.add(key)

        rows = eligible(cached)
        rows.sort(
            key=lambda x: (x.get("period_end") or "", x.get("available_at") or ""),
            reverse=True,
        )
        return rows

    def facts(self, symbol, as_of, max_filings=8):
        facts = []
        for filing in self.catalog(symbol, as_of)[:max_filings]:
            url = filing["xbrl_url"]
            if url in self._xbrl_cache:
                facts.extend(self._xbrl_cache[url])
                continue
            try:
                r = self._get(url)
                parsed = _parse_xbrl(r.content, filing)
                self._xbrl_cache[url] = parsed
                facts.extend(parsed)
            except Exception:
                continue
        cutoff = _dt(as_of + " 23:59:59")
        # XBRL contexts can contain comparative or forecast periods. Exclude any
        # fact whose period end is after the decision date, even if its filing
        # envelope is otherwise available by that date.
        return [r for r in facts if _dt(r.get("available_at")) and _dt(r.get("available_at")) <= cutoff
                and _dt(r.get("period_end")) and _dt(r.get("period_end")) <= cutoff]

def snapshot(records, as_of):
    rows = []
    for r in records:
        if not r.get("available_at"): continue
        cutoff = _dt(as_of + " 23:59:59")
        period_end = _dt(r.get("period_end"))
        if _dt(r["available_at"]) <= cutoff and period_end and period_end <= cutoff:
            x = dict(r)
            x["as_of"] = as_of
            rows.append(x)
    return rows

def write_snapshot(records, as_of, root="data/pit"):
    Path(root).mkdir(parents=True, exist_ok=True)
    path = Path(root) / f"fundamentals_{as_of}.jsonl"
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n")
    tmp.replace(path)
    manifest = Path(root) / "manifest.json"
    existing = {}
    if manifest.exists():
        try: existing = json.loads(manifest.read_text())
        except Exception: pass
    existing[as_of] = {
        "path": str(path),
        "rows": len(records),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": "NSE_XBRL",
        "pit_rule": "available_at <= as_of",
    }
    manifest.write_text(json.dumps(existing, indent=2, sort_keys=True))
    return str(path)
