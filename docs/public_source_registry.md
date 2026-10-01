# MBE Public Research Source Registry

Updated: 2026-10-01

Purpose: strengthen Multibagger Engine data resilience without changing the proprietary MBE scoring, gates, PIT rules, or promotion thresholds.

## Approved reference sources

### 1. rejinreeza/trading_tool
https://github.com/rejinreeza/trading_tool

Relevant components:
- NSE equity backtesting framework
- point-in-time fundamentals using filing availability dates
- sector-neutral fundamental ranking
- explicit coverage tracking
- cost-aware validation
- benchmark comparison and anti-overfitting guidance

MBE use:
- methodology/reference implementation for PIT validation and independent backtest checks.
- Do not import its strategy scores into MBE.

### 2. dhananjaym182/india-xbrl-filings
https://github.com/dhananjaym182/india-xbrl-filings

Relevant components:
- NSE/BSE XBRL filing discovery
- byte-exact raw payload preservation
- SHA-256 provenance
- append-safe manifest/checkpoint
- revision preservation
- offline integrity audit

Important:
- Repository states personal/educational use only and warns that exchange endpoints are unofficial.
- Therefore this is a reference/fallback architecture, not a silently embedded production data dependency.

MBE use:
- filing provenance, revision handling, raw-evidence archival design, and independent PIT audit reference.

### 3. pkjmesra/PKScreener
https://github.com/pkjmesra/PKScreener

Relevant components:
- technical screening
- momentum
- volume
- breakouts
- RSI/MACD
- VCP/consolidation patterns

MBE use:
- independent technical-discovery cross-check/fallback.
- Never replaces the MBE early-entry and six-month firewall.

### 4. anandbaid/promoter-watch
https://github.com/anandbaid/promoter-watch

Relevant components:
- promoter transaction disclosures
- PIT dissemination timestamps
- revisions
- promoter buying separated from transfers, pledges and other non-equivalent events
- promoter transaction value normalized against liquidity

MBE use:
- governance/catalyst evidence cross-check.
- Promoter activity remains evidence, not an automatic buy signal.

## Integration safety rules

1. Existing MBE PIT store remains authoritative for promotion.
2. Public repositories are reference/fallback layers unless separately validated.
3. No public repository may lower MBE gates.
4. No current fundamental value may be substituted for historical PIT data.
5. Six-month run-up firewall remains unchanged: final candidates require ret_126d <= 50%.
6. Trap Firewall, catalyst gate, evidence gate and PIT promotion gate remain unchanged.
7. Public-source discrepancies are logged rather than silently merged.
8. Revisions must be preserved; no overwrite of prior filings.
9. Independent technical screens are discovery inputs only, not final MBE scores.
10. Historical validation and live discovery remain separate.

## Current architecture

Market data:
TejHQ/NSE PIT market history -> independent technical fallback/reference

Fundamentals:
NSE XBRL PIT store + dynamic PIT ingestion -> public XBRL reference/audit

Technical discovery:
Chartink -> local MBE scanner -> PKScreener reference/fallback

Fundamental cross-check:
Screener/public data -> PIT evidence gate

Governance:
NSE corporate announcements + promoter-watch methodology

Final decision path:
six-module fundamentals -> Early Inflection -> MBE score -> Trap Firewall -> Catalyst -> six-month firewall -> PIT promotion.

## Change-control

This registry is deliberately documentation-only. It does not modify scoring or gate logic. Any future code adapter must be isolated, tested offline, and validated against the existing MBE outputs before activation.
