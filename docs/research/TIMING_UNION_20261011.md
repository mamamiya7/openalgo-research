# Shared native timing evidence — 11 October 2026

## Purpose and implemented scope

Before an optimiser compares entry or exit clocks, every admitted clock must
have its required prices available on the same recorded calendar. This increment
implements that internal dependency. It does not enable a new automatic recipe,
add an endpoint or change the existing Optimize action.

`research/timing_search.py` proposes a bounded original reference plus meaningful
native clock alternatives, builds their shared price union, freezes the evidence
cohort and materializes one fixed branch for qualification. The original account,
allocations, sizing, fees, horizon and holding ranges remain unchanged. Choices
that produce no distinct entry or deadline for these observations are removed
without using returns or any other outcome.

Daily-only inputs remain daily and do not admit this minute timing stage.
Supported timed and mixed inputs retain their CSV observation semantics. The
qualified engine is VectorBT and the asset scope remains long NSE cash equity.
Recorded native sessions and actual opening/closing hours are mandatory, including
short and special sessions. Missing hours, provenance or a complete holding tail
cannot be replaced with guessed weekdays or default session hours.

## One union, one opportunity cohort

For each source observation, planning records each branch's causal entry and
maximum applicable deadline. Overlapping symbol windows are merged and shared
candles are counted once. Bounds are checked before minute arrays or engine
matrices are allocated. A pending calendar blocks price preparation.

An entry rejected by a clock rule belongs to that branch's policy. It does not
remove the observation from the original reference. A genuinely missing candle
inside any scheduled branch's required window excludes the affected source
opportunity from every branch. The frozen receipt records the symbol, date,
branch and missing candle; source rows and prior exclusions are preserved.
Candidates therefore cannot benefit from different missing-price cohorts.

`services/research_timing_prices.py` stages this union through the existing native
Historify reader, writer and historical-price acquisition service. It reuses
cached candles, requests missing windows through OpenAlgo's broker history path
and retains ordinary broker pacing and bounded checkpoint manifests. It creates
no second downloader, database or per-trial acquisition loop.

The staged contract binds normalized source/configuration identity, execution
versions, native calendar, registered choices and exact union. Resuming different
inputs is rejected. A completed prepared checkpoint reopens frozen evidence
without another download. Contract references use the existing artifact closure
keys so research storage maintenance retains them with the checkpoint.

## Compatibility and remaining admission gates

Existing v2–v6 submission, acquisition, search and saved-result paths are
unchanged. Fixed branch materialization is an internal qualification interface;
it does not relabel a v6 study as clock optimisation. Broader market/stock context
preparation and indicator warm-up still belong to their existing native services.

Before admitting `automatic-hypotheses-v7`, integrate and qualify bounded timing
selection with joint conditions/trailing search, causal feature context, the
proposal/calculation budget, chronological and cost checks, pause/resume through
the real worker, findings, chosen-setup reuse and exact replay. Public-main
integration and complete Windows/Linux release acceptance remain separate gates.

Current activation/publication status belongs in [STATUS.md](STATUS.md).
The earlier diagnostic contract is [native timing admission](TIMING_ADMISSION_20261011.md).

## Qualification

18 core and 12 native integration cases pass. They cover regular and special
session parity, branch-specific policy filters, common missing-price exclusions
in actual VectorBT ledgers, cache reuse, committed-window pause/resume, altered
source/calendar/contract rejection, bounds before I/O, failed authentication and
cancellation. 48 existing timing/preflight/native-price/portfolio cases and 45
automatic-catalog cases also pass: 123 distinct backend cases in this increment.
All 160 source compatibility checks, changed Python Ruff and Git whitespace checks
pass. No frontend files, engine pins or ordinary execution paths changed.

Controlled tests use an isolated real native Historify DuckDB and deterministic
broker responses; they do not certify fresh live-broker history or profitability.
The normal application remains on its already activated v6/timing-preflight
source; this internal stage is included in development source and the distribution
inventory for the next runtime update.

The current v1 catalogue retains the exact original clock settings and proposes
entry not-before clocks 15 and 60 minutes after the modal recorded native opening,
plus an intraday-only exit 15 minutes before its closing. Conflicting or
demonstrably ineffective clocks are removed; unresolved calendar tails remain
pending. Each strategy has at most four branches. There are at most eight
strategies, 25,000 observations, 3,000 recorded sessions, two million window visits
and 500,000 shared-account matrix cells. These are finite qualification choices,
not a claim to search every possible clock or find a globally optimal strategy.

Resource audit scope: the pure planner owns no resources or retained cache.
The acquisition helper delegates short-lived DuckDB connections and broker HTTP
ownership to existing native services. Its checkpoint metadata is job-local and
bounded; artifact publication releases its transaction before the worker
checkpoint callback. No new threads, executors, clients or scheduled jobs are
introduced. Cancellation, failed acquisition and changed-input resume must pass
the same scoped acceptance tests as successful preparation.
