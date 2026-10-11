# Native timing admission — 11 October 2026

## Implemented foundation

Preflight now derives a presentation-only `research-timing-admission-v1` receipt
from the actual selected CSV observations, normalized strategy rules and the full
allowed holding range. It identifies daily, timed and mixed intent, explains the
resolved account-wide D/1m interval, retains entry/exit clocks and lists competing
deadline constraints. A daily strategy in a mixed portfolio shares minute prices
without changing its next-session signal rule.

The ordinary preflight endpoint returns this receipt for fixed backtests and
optimisations. Setup keeps its explanation under **Why these candles?**, collapsed
by default. Editing inputs immediately removes the old explanation. Reading a
preview does not create a job, read Historify prices, load calendar databases or
contact a broker. Native price preparation still owns acquisition and coverage.

The pure calendar-aware API separately accepts explicitly recorded native NSE
sessions and opening/closing hours. It reports scheduled, excluded and incomplete
observations, with the first actual symbol/date/clock issue versus the recorded
calendar bounds. Calendar readiness is not OHLC coverage. Missing provenance or
unrecorded session hours remain pending; no default market hours are invented.
This calendar-aware diagnostic API is qualified independently; the current setup
preview deliberately uses the no-calendar path, so it does not claim completed
calendar or price checks.

## Financial and compatibility contract

This increment adds no searched clocks, asset class, broker downloader or engine.
The long NSE cash-equity scope remains. The normalizer, native data planner,
scheduler, fill policy, automatic v2–v6 recipe bytes and saved result identities
remain authoritative and unchanged. Capital, allocations, sizing, costs and clocks
stay fixed. A minute holding limit and a multiday session cap can both apply;
intraday session close makes the session holding count dormant. The earliest
applicable exit constraint wins, independently of stops/targets/trailing fills.

Receipts bind only bounded normalized timing/signal identities and admitted
calendar metadata. They do not serialize source CSV bytes, prices or arbitrary
source-receipt payloads into the diagnostic identity. All state is local: at most
eight strategies,25,000 observations and3,000 recorded sessions, with no retained
cache, engine allocation, connections, threads, subprocesses or file owners.

## Next qualified search increment

The bounded choice/union/cohort foundation is implemented separately in the
[shared native timing evidence contract](TIMING_UNION_20261011.md). It remains an
internal opt-in qualification stage; the current recipe does not search clocks.

1. Define bounded timing choices from the declared horizon and observed signal
   times; do not turn a daily experiment into minute execution silently.
2. Extend native requirement planning to the union of every admitted candidate
   entry and maximum deadline, including short/special sessions.
3. Freeze the opportunity cohort and price union once before Optuna. All branches
   use the same source opportunities and complete shared-account calculation;
   distinguish rule-filtered entries from unavailable evidence.
4. Qualify native timing/fill parity, pause/resume, costs, chronological checks,
   chosen-setup reuse and exact replay before admitting a new versioned recipe.
5. Keep wider venues/assets separate until actual instrument, calendar, account,
   fee and broker-history capabilities are qualified end to end.

`automatic-hypotheses-v7` remains planned. A timing receipt does not grant clock
search support or certify missing data. Current runtime/publication status belongs
in [STATUS.md](STATUS.md); the completed trailing increment is recorded in
[AUTOMATIC_TRAILING_20261011.md](AUTOMATIC_TRAILING_20261011.md).

## Qualification

25 pure timing cases pass, including385 comparisons against the native
entry/deadline scheduler on regular, short and special sessions. Seven native
CSV/preflight integration cases pass. Existing automatic catalog and chosen-rule
regressions bring the backend acceptance to89 distinct cases.80 frontend setup
and journey cases pass; TypeScript and the production frontend build pass.
Structural source compatibility checks and changed Python lint pass.

Resource audit: the pure receipt adds no descriptor owners or retained caches;
preview uses the existing source reader and bounded query lifecycle. Input identity
omits original CSV bytes and OHLC arrays. The frontend retains the existing
abortable, debounced account-specific query with finite stale/garbage-collection
windows. Installation and public-main merge remain separate from this source
qualification; dated activation evidence is recorded in STATUS.md.

Normal-app activation also passes:59 saved runs, original artifact identities and
configuration preserved;162 installed compatibility checks pass. The existing
2020-signal/143-symbol setup resolves to daily candles in Chrome, with the new
explanation collapsed initially and readable when opened. No research job or
fresh broker acquisition was started by this check. Development source is pushed
in draft PR6; public main and preview.7 remain separate from local activation.
