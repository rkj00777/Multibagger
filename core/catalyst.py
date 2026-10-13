import re
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://www.nseindia.com"
URL = BASE + "/api/corporate-announcements"

# Terms are used only to classify commitment quality, never to award positive
# credit by themselves.
PROMOTIONAL = (
    r"\b(non[- ]binding|exploratory|expression of interest|in principle|"
    r"proposed|proposal|strategic intent|subject to definitive agreement|"
    r"memorandum of understanding|\bmou\b|letter of intent|\bloi\b|"
    r"pilot project|feasibility study|discussions with|in talks with)\b"
)
BINDING = (
    r"\b(purchase order|work order|binding contract|contract awarded|"
    r"order received|firm order|definitive agreement|letter of award|"
    r"award of contract|contract signed|\bloa\b)\b"
)
NEGATIVE_PATTERNS = (
    r"\b(resign(?:ation|ed)?|default(?:ed)?|fraud|pledge invocation|downgrade|insolvency|wilful defaulter)\b",
    r"\b(order|contract|project|purchase order)\s+(?:was\s+)?(?:cancelled|canceled|withdrawn|terminated)\b",
    r"\b(?:cancelled|canceled|withdrawn|terminated)\s+(?:order|contract|project|purchase order)\b",
    r"\bdelay in (?:execution|delivery|payment|commissioning|completion|order fulfilment|order fulfillment)\b",
    r"\b(?:penalty imposed|forensic audit|investigation into|regulatory investigation)\b"
)
DATE_KEYS = ("available_at", "broadcast_datetime", "announcement_date",
             "announcementDate", "an_dt", "date", "dateTime", "timestamp")
TEXT_KEYS = ("text", "desc", "description", "subject", "title", "headline",
             "details", "attchmntText", "announcement", "purpose")
VALUE_KEYS = ("contract_value_cr", "order_value_cr", "order_value_crore",
              "contract_value_crore", "order_value", "contract_value")
REVENUE_KEYS = ("annual_revenue_cr", "revenue_cr", "annual_revenue",
                "revenue")
CUSTOMER_KEYS = ("customer_name", "client_name", "named_customer", "counterparty")
OFFICIAL_TYPES = {"NSE_CORPORATE_ANNOUNCEMENT", "BSE_CORPORATE_ANNOUNCEMENT",
                  "NSE_XBRL", "BSE_XBRL", "OFFICIAL_EXCHANGE_FILING"}


def _item_text(item):
    if isinstance(item, dict):
        return " ".join(str(item[k]) for k in TEXT_KEYS if item.get(k) is not None)
    return str(item or "")


def _number(item, keys):
    if not isinstance(item, dict):
        return None
    for key in keys:
        value = item.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            value = float(str(value).replace(",", "").replace("₹", "").strip())
            if value >= 0:
                return value
        except (TypeError, ValueError):
            continue
    return None


def _has_customer(item):
    if not isinstance(item, dict):
        return False
    return any(str(item.get(k, "")).strip() for k in CUSTOMER_KEYS)


def _date_value(item):
    if not isinstance(item, dict):
        return None
    for key in DATE_KEYS:
        value = item.get(key)
        if value is None or not str(value).strip():
            continue
        if isinstance(value, (int, float)):
            try:
                return datetime.utcfromtimestamp(float(value)).date()
            except (ValueError, OverflowError, OSError):
                continue
        raw = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
                    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                    "%d-%b-%Y", "%d-%b-%Y %H:%M:%S"):
            try:
                return datetime.strptime(raw[:26], fmt).date()
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return None


def assess_catalyst(items, as_of=None):
    """Conservative, explainable materiality gate. Missing evidence is unknown, not positive."""
    as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date() if as_of else datetime.utcnow().date()
    if not items:
        return {"score": 20.0, "status": "NO_VERIFIABLE_CATALYST",
                "materiality_ratio": None, "reasons": ["No retrievable catalyst evidence"],
                "verified_events": 0, "excluded_events": 0}
    assessments = []
    excluded = 0
    for item in items:
        text = _item_text(item).lower()
        if not text.strip():
            excluded += 1
            continue
        source_type = str(item.get("source_type", "")).upper() if isinstance(item, dict) else ""
        event_date = _date_value(item)
        official = source_type in OFFICIAL_TYPES
        timestamp_ok = event_date is not None and event_date <= as_of_date
        if not official or not timestamp_ok:
            excluded += 1
            continue
        if any(re.search(pattern, text, re.I) for pattern in NEGATIVE_PATTERNS):
            assessments.append({"kind": "negative", "ratio": None, "reasons": ["Negative event keyword in timestamped official filing"]})
            continue
        if re.search(PROMOTIONAL, text, re.I) and not re.search(BINDING, text, re.I):
            assessments.append({"kind": "promotional", "ratio": None, "reasons": ["Non-binding, exploratory, MoU/LOI or proposal language; no positive credit"]})
            continue
        if not re.search(BINDING, text, re.I):
            assessments.append({"kind": "unverified", "ratio": None, "reasons": ["No explicit binding order/contract evidence"]})
            continue
        order_value = _number(item, VALUE_KEYS)
        revenue = _number(item, REVENUE_KEYS)
        ratio = order_value / revenue if order_value is not None and revenue and revenue > 0 else None
        customer = _has_customer(item)
        if ratio is not None and ratio >= 0.20 and customer:
            assessments.append({"kind": "material", "ratio": ratio, "reasons": ["Binding evidence", "Disclosed value is at least 20% of disclosed annual revenue", "Named customer field present"]})
        elif ratio is not None and ratio < 0.20:
            assessments.append({"kind": "immaterial", "ratio": ratio, "reasons": ["Binding evidence but disclosed value is below 20% of annual revenue"]})
        else:
            assessments.append({"kind": "incomplete", "ratio": ratio, "reasons": ["Binding language found but value/revenue denominator or named customer evidence is incomplete"]})
    if not assessments:
        return {"score": 20.0, "status": "UNVERIFIED_OR_STALE_EVIDENCE",
                "materiality_ratio": None, "reasons": ["Evidence missing official source type or timestamp at/before decision date"],
                "verified_events": 0, "excluded_events": excluded}
    priority = {"negative": 0, "material": 5, "immaterial": 4, "incomplete": 3, "promotional": 2, "unverified": 1}
    best = sorted(assessments, key=lambda x: priority[x["kind"]])[0]
    score_map = {"material": 85.0, "immaterial": 45.0, "incomplete": 40.0,
                 "promotional": 20.0, "unverified": 25.0, "negative": 0.0}
    return {"score": score_map[best["kind"]], "status": best["kind"].upper(),
            "materiality_ratio": best["ratio"], "reasons": best["reasons"],
            "verified_events": len(assessments), "excluded_events": excluded}


def catalyst_score(items, as_of=None):
    # Compatibility wrapper: unlike the previous keyword counter, positive
    # scores require timestamped official evidence and a verifiable commitment.
    return float(assess_catalyst(items, as_of)["score"])


class NSECatalyst:
    def __init__(self, workers=8):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*", "Referer": BASE + "/"})
        self.workers = workers
        self.s.headers.update({"Accept-Language": "en-US,en;q=0.9", "Origin": BASE})
        try:
            # Prime the public filings page used by the exchange UI before its JSON endpoint.
            self.s.get(BASE + "/companies-listing/corporate-filings-announcements", timeout=15)
        except Exception:
            pass

    def one(self, symbol, as_of):
        try:
            d = datetime.strptime(as_of, "%Y-%m-%d")
            p = {"index": "equities", "symbol": symbol,
                 "from_date": (d - timedelta(days=45)).strftime("%d-%m-%Y"),
                 "to_date": d.strftime("%d-%m-%Y"), "page": 1, "size": 50}
            r = self.s.get(URL, params=p, timeout=15)
            if not r.ok:
                return [{"symbol": symbol, "source_type": "NSE_RETRIEVAL_ERROR",
                         "retrieval_error": "HTTP_" + str(r.status_code), "text": ""}]
            data = r.json()
            rows = data if isinstance(data, list) else data.get("data", [])
            out = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item = dict(row)
                item["symbol"] = item.get("symbol") or item.get("sym") or symbol
                item["source_type"] = "NSE_CORPORATE_ANNOUNCEMENT"
                out.append(item)
            return out
        except Exception as exc:
            return [{"symbol": symbol, "source_type": "NSE_RETRIEVAL_ERROR",
                     "retrieval_error": type(exc).__name__, "text": ""}]

    def batch(self, symbols, as_of):
        out = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = [ex.submit(self.one, str(s).upper(), as_of) for s in symbols]
            for future in as_completed(futures):
                try:
                    out.extend(future.result() or [])
                except Exception:
                    pass
        # Deliberately no Screener fallback for catalyst approval: current page
        # text is not a reliable timestamped primary-source event archive.
        return out
