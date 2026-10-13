# MBE fallback plan: evidence-first completion path

Status updated: 2026-10-13. This plan replaces repeated retries of a historical source that is returning zero facts. It preserves free-only data, blind scanning, the current scoring weights and the existing candidate thresholds.

## Decision

The NSE historical-XBRL backfill is **not a usable historical PIT dataset today**. The latest run requested 1,360 symbols, received 0 facts, and had 0.0% coverage. A workflow can finish successfully while its evidence job has no useful data; therefore the report's coverage gate, not the Actions conclusion, is authoritative.

Do not keep rerunning the same endpoint as if that could create evidence. Use a two-track plan.

## Track 1 — PIT fundamentals: use two different jobs for two different time periods

### A. Historical facts: recover only from dated public filings

1. Build a filing manifest from the public NSE corporate-results endpoint and BSE corporate-announcement/financial-results pages. Capture symbol/ISIN, reporting period, publication/broadcast timestamp, filing URL, source, retrieval time, and parser status.
2. Parse XBRL/iXBRL only when the original public-availability timestamp is present and verifiable. Store raw source hashes and normalized facts in append-only JSONL/Parquet.
3. Query archive URLs directly and independently of the current NSE site session. The public NSE results API exposes filing dates and XBRL links; availability of an old linked document must be tested rather than assumed.
4. When a timestamped historic filing cannot be retrieved, mark the symbol/date as PIT_UNAVAILABLE. Never backfill the gap with today's Screener/NSE values or with a later restatement.
5. Report coverage by date, symbol, metric, exchange and filing age. Historical fundamentals are eligible for model validation only once the coverage is sufficient and not systematically biased toward surviving/currently successful companies.

### B. Forward PIT snapshots: begin accumulating reliable evidence now

At each live run, persist all available filings and facts with available_at, period_end, source_url, source hash, and ingestion timestamp. Append; do not overwrite historical values. Add revision records as new facts with their own availability timestamp. This creates a genuine forward PIT ledger even where old archives are missing.

**Completion criterion:** no missing facts silently become zeros; all facts used by a decision are reproducible from an archived source available on or before that date; coverage is visible in every report. The historical track can remain incomplete while the forward ledger accumulates.

## Track 2 — Historical predictive validation: split what can be tested now from what cannot

### A. Run a price-only historical falsification test now

Use the free TejHQ NSE daily price history (available from 2010) to test the discovery/technical arms independently. Reconstruct each date's eligible universe from the price file available at that date; compute only features that existed on that date; label 6-, 12- and 24-month total-return outcomes afterward. Include delisted/dead names where the source permits; disclose survivorship limitations if it does not.

Compare the blind momentum/liquidity baseline, the proposed early-trend-transition screen, each discovery arm and their union, and matched same-date/same-sector controls.

Report pre-run winner recall, top-decile lift, 2x/3x incidence, drawdown, time-to-hit, transaction-cost sensitivity, confidence intervals clustered by decision date, and false-discovery control. Do not feed any future outcome back into score construction.

### B. Validate fundamentals as a separate walk-forward cohort

Start from the forward PIT ledger. Use quarterly decision dates, fixed weights and gates, and only scores that can be reconstructed from facts already available on that date. Do not claim a historical fundamental backtest until enough independent dates have matured. The existing minimum of eight independent dates is a floor for an initial statistical check, not proof by itself. The 12-month and 24-month cohorts mature later.

**Completion criterion:** the 6-month and 12-month horizons each have at least eight independent matured decision dates, positive out-of-sample selection lift with a confidence interval excluding zero, stable sector/date diagnostics, and no material data-coverage bias. Keep 24-month results explicitly NOT_MATURE until outcomes exist.

## Track 3 — Automatic promotion: separate candidate selection from production approval

The engine now has two levels:

1. **Shadow-qualified candidate:** passes the existing candidate-level gates (MBE >=65, fundamental >=60, evidence >=0.75, catalyst >=60, Trap Firewall PASS, PIT verified, and six-month return <=50%). It can be shown in the paper-trading/monitoring list, not treated as a validated investment recommendation.
2. **Production-promoted candidate:** the same candidate-level gates pass and the latest archived validation report explicitly reports VALIDATED_FOR_PRODUCTION; both 6- and 12-month horizons are VALIDATED, each has >=8 independent dates, and each selection-lift confidence interval lower bound is >0.

If the validation report is missing, malformed, insufficient, or negative, production promotion fails closed. The report must state why. This gate is separate from workflow success and cannot be bypassed by candidate count.

## Implementation completed in this commit series

- Added multibagger/promotion_policy.py to fail closed on missing or inadequate validation.
- Updated multibagger/engine.py to report shadow_promoted_candidates separately from promoted_candidates, with promotion_mode and a machine-readable blocking reason.
- Added tests for missing reports, insufficient dates/negative lift, and the validated case.
- The original MBE score weights and candidate thresholds are unchanged.

## Known blockers—not hidden

- Current historical NSE XBRL backfill: 0 facts / 1,360 symbols; unusable.
- Latest PIT validation: 53 stock-date observations across 3 independent decision dates; status INSUFFICIENT_INDEPENDENT_SAMPLE. Six-month selection-lift CI crosses zero. Twenty-four-month outcomes are not mature.
- Therefore the engine may run live discovery and paper-trading, but production promotion must remain disabled until the explicit validation gate passes.

## Data sources and free-only policy

- NSE public corporate financial-results API: https://www.nseindia.com/api/corporates-financial-results?index=equities&period=Quarterly
- TejHQ Indian Markets daily NSE data: https://huggingface.co/datasets/tejhq/indian-markets
- BSE public corporate filings: https://www.bseindia.com/corporates/ann.html

All data retrieval remains free. No paid vendor is required. If a source is unavailable or its historical timestamp cannot be established, mark the gap and use the fallback track rather than manufacture evidence.