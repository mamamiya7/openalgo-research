# Turnover constraints and recommendation evidence — 21 October 2026

## Scope

The next core increment follows [entry-priority research](PRIORITY_CORE_20261021.md).
New unconfigured automatic studies select `automatic-hypotheses-v12`. Saved v11
and earlier recipes keep their identities. This implements a bounded package 4
turnover constraint and the first package 5 recommendation/uncertainty controller;
it does not complete liquidity/capacity or every planned robustness diagnostic.

## A fixed daily buy budget

Execution policy `research-execution-aware-v3` requires risk limits v2 and native
VectorBT adapter/policy v4. The account-wide `max_daily_buy_turnover_pct` defaults
to 100 and accepts values above zero through 1000. It is an explicit user ceiling,
never an optimised parameter. All strategies share its exact value and version.

At the first recorded opening of each trading date, freeze account cash plus
carried positions marked at their observed openings. The daily budget is that
opening equity times the declared percentage. Actual filled buys consume slipped
price times quantity, excluding fees. Rejected orders consume nothing. Whole-share
entry capacity is clipped by the remaining buy budget and every existing cash,
exposure, position and stop-risk limit. Sells do not replenish the budget.
Protective, time and other exits are never blocked. Subsequent minute prices,
sale proceeds or realised profits cannot change that date's denominator.

This is a one-sided entry-activity constraint, not annualised turnover or proof of
market liquidity. A value above 100 permits bounded same-day recycling; it does
not grant leverage or relax other limits. No additional broker/indicator data is
required. Native receipts retain each day's opening, equity, budget, filled buy
notional, fill count and remainder, plus the entry's available capacity.

## Recommendation before the final check

The search retains 108 entry hypotheses, four independent fits of 68 proposals
and the existing 384-account ceiling. At most six additional development accounts
are reserved: the final development winner/reference at higher costs, and up to
four prespecified one-step neighbouring settings. The conservative attempt bound,
including controls, ordinary validation, reconstruction, final verification and
64 lost attempts, is 365. No validation capacity is borrowed for extra proposals.

Only a non-baseline winner whose three chronological and higher-cost checks pass
is assessed further. Its paired daily account evidence must show positive return
and improvement in return minus drawdown, with maximum drawdown at most 25%.
All three later-window pairs remain separate fresh-cash accounts; they assess the
refitting procedure, not a continuous portfolio stitched from different winners.
The main comparison needs at least 60 observed sessions, and the three later
windows need at least 10 each and 60 total, without overlap.

Two deterministic paired circular-block analyses use 5- and 10-session blocks,
512 resamples each and seed zero. Both 2.5th-percentile mean daily-return edge
estimates must exceed zero. Actual account dates remain paired, including their
cross-symbol/shared-cash dependence; trades, symbols and trial counts are never
treated as independent observations. Input bounds are 4096 sessions and 100,000
raw marks. Intraday observations aggregate to actual last observed session marks,
without inventing missing bars or closes.

At least two and at most four actual neighbouring accounts are needed. The
deterministic plan balances active holding, trailing and ATR protection axes;
inactive percentage exits cannot supply fake neighbours. At least 75% must pass
the ordinary return/risk gates and the median return edge must retain at least
half of the selected edge. Neighbours share its condition and hard ceilings.
This checks a declared local subset, not exhaustive parameter stability.

The full threshold policy is frozen in the recipe. Missing, incompatible,
insufficient or failed evidence keeps the unchanged settings as the reference.
Provably short signal spans fail before acquisition, and too few recorded
later-window sessions fail before the first optimisation account is calculated.
The compact assessment is persisted and reproduced on resume before a final
account is admitted. Final outcomes cannot choose or replace the settings.

## Comparable opportunities, actual different admissions

Retained v12 development accounts additionally bind the canonical original
opportunity basis, before candidate filters or settings change admissions.
Different actual cohorts are allowed only under an identical verified original
basis, common source observations/prices/calendar/instruments, dates, starting
capital, costs and native execution assumptions. Both actual admission IDs remain
in the assessment. Only the exactly declared joint-v2 entry condition may differ
inside execution metadata; its hypothesis must match the saved account.
Changed engines, adapters, clocks, fees or undeclared conditions cannot pass.

The existing immutable evidence store owns these histories. A read loads and
verifies one owned bounded shard, with no persistent curve cache. Old shards
without this optional field retain their original encoding and identities.

## What the user sees

The existing portfolio limits include **Daily buy budget %**. Saved settings and
the trade report retain its actual value and explain that exits stay unrestricted.
The existing collapsed Research details show recommendation check results and
the historical daily edge ranges. Healthy operation does not gain extra banners
or another configuration page. Explicit older scopes remain available.

The resampled ranges are conditional descriptive sensitivity estimates under
approximate short-range stationarity. They are not search-adjusted confidence,
a probability of future profit, an estimated false-discovery rate or a universal
optimum. Adaptive search and regime changes can still produce false positives.

## Qualification

515 distinct scoped backend checks and 237 frontend checks pass, together with
Python lint, TypeScript, source compatibility and a production build. The ignored
`qualification.json` records the deduplicated backend receipts.

Isolated native tests cover daily/minute budgets, carried positions, first-opening
denominator freeze, fees/slippage, multiple strategies/priorities, same-bar and
opening exits, rejected/no-fill orders, ATR/stop-risk composition and all other
limits. Four complete legacy report fingerprints remain unchanged. Three resource
repetition cases pass; 100 turnover accounts keep Windows handles 1029 to 1029
and reach a stable RSS plateau (+434,176 bytes across the measured run).

The recommendation suite covers no-edge and correlated temporal fixtures, sparse
histories, lucky jumps, shifting markets, parameter cliffs, actual native-gate
enrichment, different fold choices, incompatible facts, final-period rejection
and deterministic ranges. Those fixtures are regressions, not statistical
calibration. The complete service journey qualifies a positive case, durable
pause/resume, full retained histories, a frozen pre-final decision and identical
completed recovery without new accounts. Saved coverage and preflight recognise
the new frozen contracts.

A full real VectorBT 0.28.5 / Optuna 5.0.0 programme completes 272 proposals and
284 native accounts in 145.36 seconds (48.19 seconds in native accounting), using
synthetic prices and real causal gates. It correctly keeps the reference on its
no-supported-improvement fixture. This qualifies orchestration and native
semantics, not broker acquisition or profitable live trading. Test receipts are
under ignored `.agent-native/turnover-core-20261021/`,
`.agent-native/turnover-native-20261021/` and
`.agent-native/recommendation-core-20261021/`.

No broker request/order, normal research job or live strategy was started during
qualification or activation. Local activation of `ee5279766` is verified at port
5000: existing account, newly served UI, authenticated method route, source
compatibility and a fresh calculation-worker heartbeat pass. All 63 saved run
identities, configuration and Historify bytes are unchanged. A rollback source/UI
snapshot is retained under the installation's ignored
`.agent-native/turnover-core-20261021/before`. The source is pushed to
`codex/native-research-refinement`; public release ZIPs remain unchanged.

## Remaining core work

Portfolio capacity still needs qualified volume/units, and correlation-aware
allocation needs its own constrained account tests. Package 5 still includes a
small feasible return/risk/activity trade-off set, richer simplicity-aware
selection and broader search-seed/false-discovery calibration. Broader learned
regimes and presentation polish follow those core dependencies.
