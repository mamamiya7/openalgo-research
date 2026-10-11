# Automatic trade timing — 11 October 2026

## Trader journey and supported scope

New, unconfigured VectorBT optimisations use `automatic-hypotheses-v7`, labelled
**Stock, market and trade timing**. One Optimize action prepares applicable data,
compares settings, checks finalists and saves findings. Older saved automatic
recipes and custom searches retain their scope. Daily signals stay daily; a
minute run admits clock choices only when they actually change a scheduled entry
or deadline. The current engine/asset qualification remains long NSE cash equity.

Research uses OpenAlgo's native calendar, Historify archive and broker history
services. It does not create a second price database or download prices per trial.
The [shared timing foundation](TIMING_UNION_20261011.md) plans one bounded union
of required minute candles before any clock candidate is scored. Stock and market
feature warm-up remains handled by the existing native context service.

## Frozen choices and fair comparisons

Each strategy retains its exact original clocks and at most three meaningful
alternatives: entry no earlier than 15 or 60 minutes after the modal recorded
opening, and an intraday exit 15 minutes before its recorded closing. Conflicting
or ineffective alternatives are removed using scheduling alone. Missing hours or
a holding tail block admission rather than inventing prices or session hours.

Every scheduled branch uses the same source opportunities and frozen prices.
Missing candles exclude the same opportunity across branches; intentional clock
filters stay branch-specific. Period boundaries purge opportunities against
**all** scheduled branches' maximum deadlines. Causal stock/market gates run after
the candidate's clocks are installed, using information available before entry.

The unchanged reference preserves cash, allocations, sizing, fees, original
clocks and dormant trailing settings. Those risk assumptions are not searched.
V7 retains joint stock/market conditions, target, stop, holding-limit and trailing
off/on selection from v6, adding registered clock choices to the same bounded
Optuna study.

## Search and validation budget

The budget remains **128 proposals and at most 320 native calculations**:

1. 96 original-clock family screens compare the 32 registered condition families
   with trailing off, low and high.
2. At most 24 independent clock probes change one strategy's clock at a time;
   remaining axes use legal grid midpoints.
3. The remaining proposals use seeded joint TPE refinement, with at least eight
   refinement proposals even for eight strategies.

Duplicate settings reuse saved calculations. Later-period checks, increased-cost
checks and bounded neighbouring settings challenge finalists. With insufficient
history, results remain exploratory. The app reports the strongest supported
candidate or retains the original; this is not exhaustive clock discovery or a
guarantee of future performance.

## Saved evidence, inspection and recovery

The recipe, acquisition checkpoint and study bind source identity, actual native
hours, registered choices, union prices, common cohort and numerical versions.
Resume rejects changed evidence and reuses the completed native preparation.
Activity capture accepts bounded clock states; scientific validation admits only
the choices in the frozen recipe. Recorded Optuna plots use that same contract.

Findings and Settings show the selected entry/exit clocks beside other trading
rules. Trial inspection, exact replay and saved setups preserve them. Replaying a
selected candidate slices its original evidence before installing its selected
settings; it does not regenerate choices from the changed setup or download prices
to inspect an existing result. Saved v2–v6 studies remain on their original paths.

## Qualification and delivery

234 distinct backend cases, 173 frontend cases, TypeScript, the production build
and 161 source compatibility checks pass. A controlled native short-session study
completed all 128 proposals and 166 calculations, including process restart from
102 committed trials. The original was retained when profitable clocks proved
fragile. Exact selected-state replay matches the saved summary, ledger, equity
and settings; an inspected 09:30 candidate reproduces its trial summary and actual
entries, and its named setup preserves the clock. Regular-session preparation
and recovery also passed. Tests cover omitted clock fields, true native branch
normalization, daily/no-effect admission, immutable evidence and legacy scopes.

Current source, native worker acceptance and normal-app activation are recorded
in [STATUS.md](STATUS.md). Controlled fixtures exercise actual VectorBT, Optuna,
Historify and worker persistence; they do not certify fresh broker downloads or
real-market returns. Public preview.7, public-main integration and the complete
Windows/Linux release matrix remain separate delivery gates.

Resource review is scoped to the changed paths. Pure timing logic has no retained
cache or owned handles. Native preparation delegates short-lived DuckDB and HTTP
ownership to existing services, releasing database transactions before callbacks.
Activity writes retain their worker lease fence and context-managed sessions.
No new threads, executors, clients, schedules or module-level registries are added.
