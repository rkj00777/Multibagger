# PIT data provenance and validation policy

This directory stores append-only, timestamped financial facts collected from free public exchange sources.

## Source order
- NSE corporate financial-results/XBRL archive
- BSE official announcements and XBRL/iXBRL attachments as fallback
- TejHQ Indian Markets (Hugging Face) for daily historical price/volume data

## Historical integrity
A fact is eligible at decision date D only when both `available_at <= D` and `period_end <= D`. Never replace a missing historical fact with a current Screener value. Missing symbol/date combinations remain explicitly excluded in `exclusions.jsonl`.

## Validation status
A live scan or successful backfill is not proof of predictive performance. Historical ranking validation must use mature forward returns, independent decision dates, confidence intervals, non-winner controls, transaction costs, and an out-of-sample period. Production promotion remains blocked until the validation gate passes.

## Refresh note
The PIT archive has expanded since earlier validation runs. Re-run the historical validation against the latest committed archive; compare outputs only when data cutoff, candidate universe, and costs are documented.
