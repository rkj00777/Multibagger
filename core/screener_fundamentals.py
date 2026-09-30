import re
from datetime import datetime

import pandas as pd
import requests

BASE = "https://www.screener.in/company/"


def _num(v):
    if v is None:
        return None
    s = str(v).replace("\xa0", " ").replace(",", "").replace("%", "").strip()
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def _period(v):
    s = str(v).replace("\xa0", " ").strip()
    if s.upper() == "TTM":
        return None
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})", s, re.I)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)[:3].title()} {m.group(2)}", "%b %Y").date().isoformat()
    except Exception:
        return None


def _norm_label(v):
    s = str(v).replace("\xa0", " ").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def _markdown_tables(text):
    lines = [x.strip() for x in str(text).splitlines() if "|" in x]
    tables = []
    i = 0

    def cells(line):
        return [x.strip() for x in line.strip().strip("|").split("|")]

    while i < len(lines) - 1:
        if not lines[i].lstrip().startswith("|") or "---" not in lines[i + 1]:
            i += 1
            continue
        cols = cells(lines[i])
        rows = []
        i += 2
        while i < len(lines) and lines[i].lstrip().startswith("|") and "---" not in lines[i]:
            vals = cells(lines[i])
            if len(vals) >= len(cols):
                rows.append(vals[: len(cols)])
            i += 1
        if rows:
            tables.append(pd.DataFrame(rows, columns=cols))
    return tables


def _flatten(df):
    x = df.copy()
    if x.empty:
        return x
    x.columns = [str(c).replace("\xa0", " ").strip() for c in x.columns]
    for i in range(min(3, x.shape[1])):
        x.iloc[:, i] = (
            x.iloc[:, i].astype(str)
            .str.replace("\xa0", " ", regex=False)
            .str.replace(r"[+]$", "", regex=True)
            .str.strip()
        )
    return x


def _find_table(tables, labels):
    labels = {_norm_label(x) for x in labels}
    best = None
    best_hits = 0
    for t in tables:
        if t.empty:
            continue
        x = _flatten(t)
        hits = sum(
            any((_norm_label(v) == b or _norm_label(v).startswith(b) or b.startswith(_norm_label(v)))
                for b in labels)
            for i in range(min(3, x.shape[1]))
            for v in x.iloc[:, i].tolist()
        )
        if hits > best_hits:
            best, best_hits = x, hits
    return best if best_hits else None


def _row(table, label):
    if table is None or table.empty:
        return None
    target = _norm_label(label)
    for i in range(min(3, table.shape[1])):
        col = table.iloc[:, i].map(_norm_label)
        mask = col.map(lambda x: x == target or x.startswith(target) or target.startswith(x))
        q = table[mask]
        if not q.empty:
            return q.iloc[0]
    return None


class ScreenerFundamentals:
    """Current public Screener fallback. Historical periods are available, but publication timestamps are not independently PIT-verified."""

    def __init__(self, workers=8):
        self.workers = workers
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml",
        })

    def parse_markdown(self, symbol, markdown, as_of, source_url):
        try:
            tables = _markdown_tables(markdown)
        except Exception:
            return []

        pl = _find_table(tables, {"sales", "net profit", "eps in rs", "operating profit"})
        bs = _find_table(tables, {"borrowings", "equity capital", "reserves"})
        cf = _find_table(tables, {"cash from operating activity", "free cash flow"})

        if pl is None:
            return []

        label_sources = {
            "revenue": (pl, "Sales"),
            "pat": (pl, "Net Profit"),
            "eps": (pl, "EPS in Rs"),
            "ebitda": (pl, "Operating Profit"),
            "debt": (bs, "Borrowings"),
            "equity_cap": (bs, "Equity Capital"),
            "reserves": (bs, "Reserves"),
            "cfo": (cf, "Cash from Operating Activity"),
            "fcf": (cf, "Free Cash Flow"),
        }
        rows = {k: _row(tbl, label) for k, (tbl, label) in label_sources.items()}

        periods = []
        for c in pl.columns[1:]:
            p = _period(c)
            if p:
                periods.append((c, p))

        out = []
        for c, p in periods[-10:]:
            vals = {
                k: _num(r.get(c)) if r is not None and c in r.index else None
                for k, r in rows.items()
            }
            if vals.get("pat") is None and vals.get("revenue") is None:
                continue

            for metric in ("revenue", "pat", "eps", "ebitda", "debt", "cfo", "fcf"):
                if vals.get(metric) is not None:
                    out.append({
                        "metric": metric,
                        "value": vals[metric],
                        "period_end": p,
                        "available_at": as_of,
                        "source_type": "SCREENER_PUBLIC_FALLBACK_NON_PIT",
                        "source_url": source_url,
                        "symbol": symbol,
                    })

            equity = (vals.get("equity_cap") or 0) + (vals.get("reserves") or 0)
            if equity > 0:
                out.append({
                    "metric": "equity",
                    "value": equity,
                    "period_end": p,
                    "available_at": as_of,
                    "source_type": "SCREENER_PUBLIC_FALLBACK_NON_PIT",
                    "source_url": source_url,
                    "symbol": symbol,
                })
        return out

    def one(self, symbol, as_of):
        url = BASE + symbol + "/consolidated/"
        try:
            r = self.s.get(url, timeout=20)
            if r.ok and len(r.text) >= 1500:
                # This class remains useful inside GitHub/local runners as a fallback.
                # Firecrawl is the preferred live acquisition path outside GitHub.
                return self.parse_markdown(symbol, r.text, as_of, url)
        except Exception:
            pass
        return []

    def batch(self, symbols, as_of):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        out = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            fut = {ex.submit(self.one, s, as_of): s for s in symbols}
            for f in as_completed(fut):
                try:
                    out.extend(f.result())
                except Exception:
                    pass
        return out
