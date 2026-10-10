"""Free, timestamped NSE-XBRL PIT backfill for walk-forward validation.

Builds the exact technical cross-sections used by the PIT validation, unions
symbols across decision dates, and reuses one cached NSE client per symbol so
historical filings are not downloaded repeatedly at every checkpoint.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from core.market_data import nse_cross_section
from core.nse_pit import NSEPIT

DEFAULT_DATES = [
    "2022-03-31", "2022-06-30", "2022-09-30", "2022-12-30",
    "2023-03-31", "2023-06-30", "2023-09-29", "2023-12-29",
    "2024-03-28", "2024-06-28", "2024-09-30", "2024-12-30",
    "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
]


def select_symbols(as_of: str, top_n: int) -> list[str]:
    px = nse_cross_section(as_of)
    if px.empty:
        return []
    px = px.copy()
    for col in ["ret_63d", "ret_126d", "ret_252d", "avg_turnover_60d"]:
        px[col] = pd.to_numeric(px.get(col), errors="coerce")
    px["technical"] = (
        px.ret_63d.rank(pct=True) * 25
        + px.ret_126d.rank(pct=True) * 35
        + px.ret_252d.rank(pct=True) * 40
    )
    pool = px[
        (px.avg_turnover_60d >= 2_000_000) & px.ret_126d.notna()
    ].nlargest(top_n, "technical")
    return pool.symbol.dropna().astype(str).str.upper().drop_duplicates().tolist()


def fetch_symbol_history(symbol: str, dates: list[str]) -> dict:
    client = NSEPIT(timeout=25, retries=3)
    facts = []
    errors = []
    for as_of in dates:
        try:
            facts.extend(client.facts(symbol, as_of, max_filings=8) or [])
        except Exception as exc:
            errors.append({"as_of": as_of, "error": type(exc).__name__})
    # A fact can be encountered at multiple checkpoints; dedupe by its full
    # filing/period identity while preserving revisions with different dates.
    unique = {}
    for row in facts:
        key = (
            row.get("symbol"), row.get("metric"), row.get("period_end"),
            row.get("period_start"), row.get("available_at"),
            row.get("value"), row.get("source_url"),
        )
        unique[key] = row
    return {"symbol": symbol, "facts": list(unique.values()), "errors": errors}


def persist_facts(facts: list[dict], report: dict) -> int:
    root = Path("data/pit/facts")
    root.mkdir(parents=True, exist_ok=True)
    grouped = {}
    for row in facts:
        available = row.get("available_at")
        if not available or len(str(available)) < 7:
            continue
        grouped.setdefault(str(available)[:7], []).append(row)

    written = 0
    for month, rows in grouped.items():
        path = root / f"{month}.jsonl"
        existing = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    x = json.loads(line)
                except Exception:
                    continue
                key = (
                    x.get("symbol"), x.get("metric"), x.get("period_end"),
                    x.get("period_start"), x.get("available_at"),
                    x.get("value"), x.get("source_url"),
                )
                existing[key] = x
        before = len(existing)
        for x in rows:
            key = (
                x.get("symbol"), x.get("metric"), x.get("period_end"),
                x.get("period_start"), x.get("available_at"),
                x.get("value"), x.get("source_url"),
            )
            existing[key] = x
        ordered = sorted(
            existing.values(),
            key=lambda x: (
                x.get("available_at", ""), x.get("symbol", ""),
                x.get("metric", ""), x.get("period_end", ""),
            ),
        )
        path.write_text(
            "\n".join(json.dumps(x, separators=(",", ":"), sort_keys=True) for x in ordered) + "\n",
            encoding="utf-8",
        )
        written += max(0, len(existing) - before)

    report["persisted_new_facts"] = written
    report["fact_files"] = sorted(p.name for p in root.glob("*.jsonl"))
    manifest = Path("data/pit/manifest.json")
    manifest.write_text(json.dumps({
        "source": "NSE_XBRL",
        "pit_rule": "available_at <= decision_date AND period_end <= decision_date",
        "backfill_dates": report["decision_dates"],
        "symbols_selected_by_date": report["symbols_selected_by_date"],
        "unique_symbols_requested": report["unique_symbols_requested"],
        "facts_received": report["facts_received"],
        "persisted_new_facts": written,
        "fact_files": report["fact_files"],
    }, indent=2, sort_keys=True), encoding="utf-8")
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", default=",".join(DEFAULT_DATES))
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--workers", type=int, default=int(os.getenv("PIT_WORKERS", "6")))
    args = parser.parse_args()

    dates = sorted(dict.fromkeys(x.strip() for x in args.dates.split(",") if x.strip()))
    selected_by_date = {d: select_symbols(d, args.top_n) for d in dates}
    symbol_dates = {}
    for d, symbols in selected_by_date.items():
        for symbol in symbols:
            symbol_dates.setdefault(symbol, []).append(d)

    results = []
    errors = []
    workers = max(1, min(8, int(args.workers)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_symbol_history, symbol, symbol_dates[symbol]): symbol
            for symbol in sorted(symbol_dates)
        }
        for i, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                result = future.result()
                results.append(result)
                errors.extend([{"symbol": symbol, **e} for e in result["errors"]])
            except Exception as exc:
                errors.append({"symbol": symbol, "error": type(exc).__name__})
            if i % 25 == 0:
                print(f"symbols_done={i}/{len(futures)}", flush=True)

    facts = [fact for result in results for fact in result["facts"]]
    report = {
        "status": "PIT_HISTORY_BACKFILL_COMPLETE",
        "source": "NSE integrated filing/XBRL; free source",
        "pit_rule": "available_at <= decision_date AND period_end <= decision_date",
        "decision_dates": dates,
        "top_n_per_date": int(args.top_n),
        "symbols_selected_by_date": {d: len(v) for d, v in selected_by_date.items()},
        "unique_symbols_requested": len(symbol_dates),
        "symbols_with_facts": sum(bool(r["facts"]) for r in results),
        "facts_received": len(facts),
        "error_count": len(errors),
        "errors_sample": errors[:100],
    }
    persist_facts(facts, report)
    Path("reports").mkdir(parents=True, exist_ok=True)
    Path("reports/multibagger-pit-history-backfill.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
