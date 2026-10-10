# MBE Historical Winner Study and Candidate Funnel

## Implementation status — 10 October 2026

The first leakage-safe implementation is now committed on `main`.

- Historical funnel code: `scripts/run_winner_audit.py`.
- The historical audit uses three independent discovery arms: broad technical transition, PIT-fundamental confirmation, and an optional constructive-near-high arm. The arms are unioned before the unchanged downstream MBE gates.
- The historical audit does **not** query current Chartink/Screener membership. Those services remain live discovery inputs only; their current results cannot be projected backward into historical dates.
- The PIT store and NSE PIT parser now require both `available_at <= decision_date` and `period_end <= decision_date`.
- The audit does **not** use today's announcement pages to infer historical catalysts. No archived PIT catalyst store is currently available, so historical catalyst/promotion status is explicitly unverified rather than treated as a neutral score or a pass.
- Leakage and funnel unit tests passed in [CI #146](https://github.com/rkj00777/Multibagger/actions/runs/38057668797). The latest code-validation run is [CI #147](https://github.com/rkj00777/Multibagger/actions/runs/38057765817), and the updated winner audit is [run #34](https://github.com/rkj00777/Multibagger/actions/runs/38057765784). Their final outcomes must be checked before declaring the audit complete.

**Readiness distinction:** the live scanner can execute and produce a shortlist, but this is not proof of multibagger predictive power. Historical statistical validation, catalyst archival coverage, and broad survivorship-safe historical universe coverage remain separate evidence gates. No scoring weights or production thresholds were loosened.

## Purpose
Use the 27 historical winners already recorded in `scripts/run_blind_historical_test.py` as a discovery/falsification set, not as hard-coded target names. Study both successful and failed candidates at decision dates before their major moves. Do not change MBE weights or gates from retrospective anecdotes.

## Findings from the completed component-attribution run
Run: https://github.com/rkj00777/Multibagger/actions/runs/37874459967

- Workflow completed successfully and produced artifact `mbe-component-attribution`.
- Mean component contributions in the current artifact (winner observations vs matched-control observations):
  - Fundamental: 23.41 vs 23.82 (difference -0.41)
  - Earnings: 10.00 vs 10.00 (difference 0.00)
  - Early trend: 3.72 vs 6.34 (difference -2.61)
  - Acceleration: 1.77 vs 3.72 (difference -1.94)
  - Balance sheet: 7.63 vs 7.68 (difference -0.05)
  - Maturity penalty: -1.76 vs -0.44 (difference -1.32)
- These are descriptive, not statistically validated. The earnings component is frequently the neutral default 50, and the sample is winner-conditioned with six checkpoints, so this is not evidence to change weights yet.
- Coverage remains a first-order issue: the blind discovery set is capped at 500 and historical PIT coverage is materially lower than 500. Many benchmark names never enter the discovery set or lack usable point-in-time facts.
- The current discovery score emphasizes price momentum (21d/63d/126d) and liquidity. This can miss pre-breakout inflections and creates a structural bias toward already-moving stocks.
- Follow-up required: validate winner labels and dates; use non-winner controls sampled from the same date/universe; report forward 6/12/24-month returns after decision date; add confidence intervals and multiple-testing controls; separate data-coverage failures from genuine model failures.

## What likely creates multibaggers (hypotheses to test)
Treat these as hypotheses, not universal rules:
1. **Earnings inflection:** revenue growth turns into accelerating operating profit/EPS; improvement persists for multiple quarters.
2. **Reinvestment runway:** capacity, distribution, product, export or market expansion supports growth beyond one quarter.
3. **Cash confirmation:** CFO/PAT and working-capital trends confirm accounting earnings; receivables/inventory do not outrun sales persistently.
4. **Return on incremental capital:** new capacity generates rising utilization, margins and returns, rather than growth financed by dilutive or low-return capital.
5. **Expectation gap:** valuation is reasonable relative to the change in earnings power; a cheap stock alone is not enough.
6. **Balance-sheet/governance survivability:** manageable leverage, dilution, pledging, related-party exposure and auditor/promoter red flags.
7. **Price/volume confirmation:** relative strength improves from a base, liquidity is adequate, and breakout/accumulation is not a one-day spike.
8. **Catalyst path:** a dated, verifiable reason exists for estimates to change (orders, commissioning, approvals, capacity ramp, product cycle, exports).
9. **Failure modes:** one-off commodity/cycle peaks; revenue without cash; order book without margins; over-expansion; debt/refinancing; dilution; governance problems; valuation already discounting perfect execution; late entry after the majority of the move.

## Proposed funnel: parallel discovery arms, one common MBE analysis
Do not use one narrow AND screen. Run three broad, overlapping arms; union/deduplicate their symbols; then apply MBE's full analysis and Trap Firewall.

### Arm A — Early trend transition (Chartink)
Daily timeframe, liquid ordinary equity series:
- Close > SMA(20)
- SMA(20) > SMA(50) OR close crossed above SMA(50) recently
- Close > SMA(50) OR within ~5% of SMA(50) after a base
- Volume > 1.2 × 20-day average volume on confirmation day (do not require this every day)
- RSI(14) roughly 45–72
- Avoid requiring a 52-week high; that can bias discovery toward late-stage moves.
- Keep a second variant with close within 15% of 52-week high, not as a hard requirement.

### Arm B — Earnings inflection (Screener.in / Chartink fundamental fields)
Use quarterly data only as a discovery proxy; verify source/periods downstream:
- Latest quarter sales growth YoY > 15% OR accelerating versus prior quarter
- Latest quarter operating profit/EBITDA growth YoY > 20% OR margin expansion
- Latest quarter net profit growth YoY > 20%, where base effects are not distorting
- Prefer positive TTM operating cash flow; do not reject automatically when recent capex temporarily depresses CFO—flag for review.
- Exclude persistent losses, extreme leverage and repeated equity dilution where fields are available.
- Do not force a low P/E or a single valuation ceiling across all sectors; use sector-aware valuation/expectation-gap scoring downstream.

### Arm C — Reinvestment / order / capacity transition
Where screen fields support it:
- Sales growth 3-year > 10–15% OR recent growth acceleration
- ROCE/ROIC improving (or recovering from a depressed base)
- Debt/equity and interest coverage within sector-appropriate bounds
- For industrials/capex names, use disclosed order book, order inflow, capacity commissioning and utilization as evidence fields after discovery; do not infer these from price alone.

### Common post-funnel gates
1. Merge all arms; preserve which arm found each symbol.
2. Liquidity/price-history sanity and corporate-action adjustment.
3. PIT fundamentals with evidence timestamps; never substitute current fundamentals for historical snapshots.
4. Score the five existing fundamental modules and early-inflection module.
5. MBE score, maturity classification, Trap Firewall, and verified catalyst.
6. Report ranked list plus explicit rejection reason and evidence coverage.
7. Keep all scanner misses in a shadow universe for recall testing; do not permanently exclude stocks just because one screen missed them.

## Validation plan before production adoption
- Build historical decision-date snapshots from exchange/price data and PIT financial facts; no current Chartink/Screener output in historical backtests.
- Test 6-, 12-, and 24-month forward total returns, 2x/3x hit rates, max drawdown, and time-to-multibagger.
- Compare funnel arms independently and as a union against matched non-winners.
- Report recall among winners, precision/top-decile lift, coverage exclusions, and uncertainty intervals.
- Walk forward by date and sector; use FDR control for module comparisons.
- Adopt a screen only if it improves candidate quality without materially reducing early-winner recall. No weight/threshold changes from the current small benchmark alone.

## Implementation note
Chartink/Screener are discovery tools, not the source of truth for the historical backtest. Platform field names, plan availability and exact syntax must be verified in each saved screen. The engine should still retain its independent blind local arm so external-screen coverage gaps do not become permanent blind spots.
