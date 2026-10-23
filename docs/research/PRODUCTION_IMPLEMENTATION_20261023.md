# Production implementation plan â€” 23 October 2026

Status: runtime batch implemented; completed acceptance FAILED the one-minute
performance gate. Public release remains pending.
This is the immediate work order for the existing CSV/Chartink product;
broader feature development and repeated full-run profiling wait behind it.

## Implementation checkpoint â€” 24 October 2026

The complete execution policy now runs through the installed VectorBT/Numba
simulator, with the Python implementation retained as a parity reference.
One frozen 146-stock account (1,869 signals, 160 sessions) changed from 5.313
seconds in the Python reference to 0.0618 seconds warm. Its complete report
fingerprint is unchanged. The first account with an existing compilation cache
took 6.064 seconds; this does not measure an empty-cache compilation.

Prepared period owners retain immutable prices, numeric views and admitted entry
evidence with bounded lifetimes. Correlation preparation batches the same NumPy
pair operations while retaining exact directed results. Candidate processing no
longer copies and immediately discards the complete signal tree. Canonical JSON
accepts only the private immutable capability for copy reuse; ordinary mutable
inputs retain full admission and tamper checks.

Prepared rule lookup, shared observation windows and exact encoded evidence
remove repeated catalogue construction, JSON copying and encoding. Exact immutable
daily inputs can reuse identical ATR/capacity/correlation/allocation attachments;
changed sources, calendars or feature modes invalidate that reuse, and minute
schedules retain their ordinary path. Native financial schedules and audits still
run for every account. Fresh native reports transfer single-use ownership to the
same strict validator, avoiding a redundant defensive copy; saved/public/custom
reports keep their original admission.

Intermediate stock/market strategy outputs that the combined gate discards are
omitted only within the private expanded gate. All feature causality, memberships,
counts and final full-account receipt identities remain exact. Focused parity
and work-count checks cover those boundaries. Component reuse is implemented; full-journey performance qualification
remains separate from component checks. The account timing above is not an optimizer
or app timing, and no under-one-minute full-journey claim is made.

The first installed candidate completed all 408 proposals and 328 native
accounts in 158.808 worker seconds. Exclusive buckets include 66.073 seconds of
serialization/hashing, 17.174 of other gate processing, 15.434 of financial
validation, 18.590 of simulation including first compilation, 12.993 of report
construction, 2.616 of typed preparation, 1.016 of signal attachment, 3.482 of
other input preparation, 2.456 of artifact read/verification, 4.567 of disk
publication, 1.186 of database work, 3.066 of runtime loading and 9.390 other.
This misses the target. It is the concrete basis for the next corrections:
incremental reconciliation of exact immutable completed evaluations, preservation
of already canonical checkpoint/shard root bytes, and reusable per-record
observation members. Durability, canonical hashes and fresh mutable/saved-input
validation remain required. No intermediate public release is qualified.

The second installed candidate completed the same 408 proposals and 328 native
accounts in 136.963 seconds. Scientific output (4,853,542 canonical bytes,
1,869 ledger rows) exactly matches the first run: SHA-256
`49f7d6d61c3c8e2488190bbb5dbd3e0334d2f614eda7eca24df25bc139642f29`.
Disk publication fell to 0.663 seconds. Encoding/hashing remained 57.400 seconds,
other gate processing 17.234 seconds, and native constraint assessment 12.943
seconds including its then-unseparated canonical resource scan. These are
completed installed-app measurements, not a sub-minute result.

The next frozen corrections reuse sizeable exact immutable JSON subtrees within
the existing 32 MiB owner budget, reuse checked flat native ledger-row bounds
without removing financial checks, retain admitted causal feature values
immutably while public results thaw into fresh mutable data, and retain signal-list bytes across the gate and native report phases.
An attempted derived-exclusion cache was removed after the next measured run
regressed to 143.295 seconds; it is not part of the final batch. Current
account/causal receipt identities are always rebuilt.
Focused work-count, tampering, byte parity, ownership and bound checks qualify
these changes before the next installed acceptance. No search is shortened.

## Final batch outcome and remaining production gate

The final installed run completes all 408 proposals / 328 accounts in 126.506
worker seconds with identical scientific output. Exact candidate-report preparation
adds 17.601 seconds, and the tested setup saves and reopens after browser reload.
The full journey exceeds 144.107 worker seconds before UI/additional analysis.
The detailed exclusive buckets
and qualification limits are in the preview.27 candidate notes. Encoding and
canonical resource checks still cost 57.955 seconds; other gate processing costs
18.285 seconds. Simulation costs 11.371 seconds including first compilation.
The required one-minute full journey is not achieved, so this plan is not complete
and the candidate is not approved for public production release.

The next implementation must address repeated canonical traversal and receipt
materialization structurally, rather than adding more whole-candidate caches:

1. Admit static canonical fragments once, retain exact per-source signal/feature
   members, and compose changed account/config fields without walking the same
   immutable descendants again. Preserve strict mutable/saved input admission,
   node/depth/finite/byte limits and byte-for-byte canonical hashes.
2. Move repeated candidate receipt assembly/resource checks into a bounded native
   path, reusing the existing typed engine and source indices. Differential tests
   must retain every exclusion, fill, order, financial check and scientific ID.
   Any compiled implementation needs Windows/Linux packaging qualification.
3. Use small representative correctness and work-count tests first. Authorize the
   next full 146-stock acceptance only after component evidence fits the total
   budget with compilation and save/reopen headroom; do not resume a sequence of
   full runs to discover small cache tuning changes.

Features, parameters, all search proposals, adaptive proposal order and validation
stay fixed. This is remaining work, not a claim that the above native receipt
path has already been implemented.

## Outcome and fixed scope

Import signals â†’ prepare automatically â†’ run â†’ inspect â†’ save â†’ reopen.
Target under one minute for the complete cached-data automatic journey on the
146-stock scanner. Preserve features, financial semantics, all 408 proposals,
the 512-account ceiling, required validation and exact saved evidence. Ship
one reviewed batch. No new engine, search method, indicator catalogue, LLM
orchestration, asset expansion or distributed platform is needed.

The earlier small capital and CSV checks verify their own component/journey
contracts. They do not qualify full automatic research or its runtime. Keep
the capital-planning correction already implemented; do not redesign it again.

## Findings from current source and existing measurements

The user reports an approximately four-second individual backtest. Hundreds of
accounts plus preparation and validation are a different workload. The retained
preview.25 fixed-backtest journey took 58.859 seconds for 146 stocks. Preview.26
completed 408 proposals but its release notes record 1,220.113 seconds including
interruption/recovery; that is not a clean benchmark. The preview.27 large run
was cancelled at the user's request. None proves sub-minute automatic research.

Existing early/late controller profiles in ignored
`.agent-native/runtime-breakdown-20261022/summary.json` show late Optuna ask/tell
at about 0.032 seconds in that older sample, with seconds spent on surrounding
feature size/seal and processing work. These are historical scoped measurements,
not new 146-stock bucket timings. There is no reason to rerun them before coding.

| Source at the planning checkpoint | Verified initial fact | Implementation consequence |
| --- | --- | --- |
| `services/research_preparation.py::_checked_inputs` | Early preparation already checks identity, archive freshness, calendars and prepared-input integrity. | Extend this path instead of adding another prefetch service. |
| `services/research_portfolio.py::validate_prepared_inputs` | Coverage/receipt checks occur at handoff; the run re-admits derived context. | Share one checked owner through handoff/worker, preserving freshness and recovery checks. |
| `research/stock_market_context.py::_BundleValidation` / `_PreparedViews` | Bounded validation and causal-feature reuse already exist. | Keep them; ordinary indicator precomputation is not wholly absent. |
| `research/portfolio_execution_features.py::_PreparedPortfolioFeatures` | Feature seals are already shared, while schedules and actual constraint checks remain per account. | Distinguish invariant work from candidate-dependent checks. |
| `research/connectors/vectorbt_portfolio.py::validate` / `evaluate` | Each account validates/schedules inputs and allocates/fills a close matrix by time and signal. Multiple signals reuse a symbol's prices. | Prepare immutable market arrays and coverage indices once, with small candidate overlays. |
| `research/connectors/vectorbt_portfolio.py:749` | `Portfolio.from_order_func(..., use_numba=False)` explicitly runs simulation and callbacks in Python. | Adapt callbacks to the engine's existing compiled facility; flipping the flag alone is insufficient. |
| `research/automatic_walk_forward.py::_run.simulate` | Every account attaches policy/gates, evaluates, builds evidence and assesses constraints. | Reuse static inputs while retaining all account-dependent checks. |
| `research/connectors/vectorbt_portfolio.py::_report` | Development display analysis is already suppressed. | Do not propose that same optimisation again; remove only additional proven duplicate conversion/copy work. |

The refreshed Graphify runtime graph records these paths, current source hashes
and its limited scope. It does not claim the old whole-platform graph is current.
Graph centrality is not a timing measurement.

## Reuse decisions from primary sources

**Preparation â€” follow Freqtrade's structure.** Freqtrade loads historical data
and computes reusable indicators before repeated hyperopt backtests. Adopt that
division through our existing Historify and prepared owners. Do not import its
exchange infrastructure or migrate the application.
[Execution model](https://www.freqtrade.io/en/stable/hyperopt/) Â·
[optimizer source](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/optimize/hyperopt/hyperopt_optimizer.py).

**Parameters â€” keep installed Optuna 5.0.0.** It already provides seeded
ask/tell, initial parameter sets and completed-trial import. Existing screening
and replay determine the proposal order. Reuse a completed evaluation only when
data, period, objective, configuration and constraints match exactly. No new
sampler, pruning, early stopping or parallel adaptive batch is part of this fix.
[Ask/tell](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/009_ask_and_tell.html) Â·
[initial parameter sets](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/008_specify_params.html) Â·
[pinned source](https://github.com/optuna/optuna/blob/v5.0.0/optuna/study/study.py).

**Execution â€” use installed VectorBT 0.28.5 and Numba.** The engine already
separates preparation, compiled custom-order simulation and analysis. Feed it
typed arrays and compatible callbacks while retaining its order/fill accounting.
Keep strict floating-point operations and deterministic order; no `fastmath`.
The Python path remains the differential reference and old-evidence fallback.
[Pinned VectorBT source](https://github.com/polakowo/vectorbt/blob/v0.28.5/vectorbt/portfolio/base.py) Â·
[Numba guidance](https://numba.readthedocs.io/en/stable/user/performance-tips.html).

**Representation â€” use existing NumPy first.** Start with bounded, read-only
arrays owned by the current worker. NumPy's binary/memory-mapped arrays are an
option only if process/restart transfer is a demonstrated remaining cost.
PyArrow and orjson are already installed; another cache, serializer or database
is not the default answer. Preserve canonical hashes and saved artifact formats.
Any binary derived artifact needs source/shape/dtype seals, atomic publication
and Windows-safe closure. Do not add pickle-based imported cache trust.
[Binary arrays](https://numpy.org/doc/stable/reference/generated/numpy.save.html) Â·
[memory mapping](https://numpy.org/doc/stable/reference/generated/numpy.lib.format.open_memmap.html).

**Dates and funds â€” keep the domain-specific contracts.** TimeSeriesSplit
provides chronological indices and a gap, but comparable durations require
equally spaced samples; our signals are irregular and have holding horizons.
Keep native exchange sessions, complete holding windows and the existing
available-history policy instead of adding a library for a few index operations.
VectorBT's automatic initial cash simulates with infinite cash and derives cash
spent afterwards; it is not a drop-in answer for equity-dependent sizing/risk.
Keep the current analytical seed plus bounded native capital verification,
preserving the original initial rupee ticket and all hard limits.
[Split contract](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) Â·
[automatic cash semantics](https://vectorbt.dev/api/portfolio/enums/#vectorbt.portfolio.enums.InitCashMode).

These are reuse choices, not a dependency upgrade. Pin the source revision and
retain required licence/attribution notices for any fragment actually copied.
Do not copy unrelated repository defaults and call them validated trading rules.

## Automatic choices: resolve once, do not deliberate per run

Development research happens once. Production runs resolve one versioned plan
from the actual input and existing saved profile; they need no LLM call.

| Choice | Automatic rule |
| --- | --- |
| Stocks and signals | Parse CSV/Chartink once; preserve source-row and strategy identity. |
| Interval | Derive from actual timestamps and existing execution rules; daily stays daily unless a timing rule requires minute data. |
| Period | Use supplied history, native sessions and maximum permitted holding horizon. Retain historical-only findings when independent later validation is unavailable; never invent signal history. |
| Parameters | Compile existing versioned bounds and screening points once; preserve baseline, all choices and budgets. User overrides remain explicit. |
| Funds | Use the user's chosen estimate-required mode, original initial ticket, fees and unchanged hard limits; show estimate separately from verified sufficient historical capital. |
| Risk/cost profile | Carry existing explicit settings intact. Do not infer risk tolerance from profitable results. Ask once only for a truly required value absent from input/profile. |
| Result | Keep all existing native risk, sample, date-spread, concentration and validation checks; retain baseline/no-reliable-improvement when they fail. |

## Implementation order: one batch

### 1. Freeze inputs and reference behaviour

Keep the current sizing/date/capital fixes together as the candidate. Record
source/recipe identity and reuse existing measurements. Fix the comparison
fields and same frozen 146-stock input, without copying credentials or live
account databases. No new whole-search baseline run. Old saved jobs/replay keep
their recorded semantics.

### 2. Produce one reusable prepared calculation input

Extend existing prepared ownership through handoff. Materialise unique-symbol
OHLC arrays, session/time indices, row-to-symbol/strategy mappings, causal
features and coverage indices once. Use bounded period views and small settings
overlays, not a new market-data tree for every account.

Matching early Chartink preparation and Run consume the same checked input.
A target change should not rebuild prices; a longer holding horizon extends
only missing coverage. Source, calendar, archive or price changes invalidate
affected reuse. Resume/reopen re-admits stored evidence; an in-process trusted
owner is not a persistent/global validation bypass.

Keep per-candidate configuration, entry-mask, schedule, deadline, hard-limit and
fill checks. Reuse invariant OHLC/schema validation only for privately owned
immutable data. Small mutation/invalidation tests and work-count assertions must
prove that duplicate work is removed while altered evidence is still rejected.

### 3. Adapt the complete current policy to compiled VectorBT execution

Move dictionary/string lookups into prepared numeric arrays and stable codes;
compile compatible callbacks and use the native simulator. Preserve shared cash,
source/priority order, whole shares, costs, stop-first ambiguity, trailing rules,
opening-equity allocation, turnover, liquidity, correlation and every hard cap.
No supported feature may be disabled to make the path fast.

Prove parity on small binding-constraint cases, then one representative frozen
large account. Compare orders, fills/rejections, ledger, cash/equity marks,
summaries and constraint receipts. Resolve rounding/tie-order differences before
promotion. Retain cancellation/lease fencing through bounded segments or an
equivalent safe mechanism; do not sacrifice responsiveness to compile one long
uninterruptible loop. Account state stays isolated.

Cold timing includes first compilation. Use supported compilation caching and
the existing worker where suitable; never hide startup by moving the clock.

### 4. Remove remaining duplicate trial conversion and persistence work

Keep the existing suppression of development display analytics. Remove only
additional proven duplicate report/copy/hash traversals. Prefer native numeric
records plus one deterministic conversion at the artifact boundary. Retain all
scientific evidence and candidate-dependent validation. Selected/final/saved
reports keep the complete public contract.

Recovery must reconcile every attempt, proposal and artifact. Preserve quota,
lease, checkpoint and durability guarantees. Keep serial adaptive Optuna order;
parallelising it changes the experiment. Do not replace canonical integrity
hashes or increase checkpoint grouping just to manufacture a faster number.

### 5. Small correctness checks, then one final acceptance

Run affected small checks per change: immutable reuse/invalidation; callback
parity with binding caps; date/holding boundaries; capital sizing; cancellation,
recovery, tampering and save/reopen. The three-account smoke remains a smoke
test. Reuse unchanged qualification where its source remains applicable.

Check one bounded frozen candidate sequence, including pause/resume, against the
reference. Compare proposal order, account evidence and decisions. Check throughput
on representative 146-stock account shapes, not just three-stock fixtures. These
are narrow engineering gates, not a new full profiling campaign. If the path
cannot plausibly meet the budget below, fix the named gap before a full search.

Only then perform one installed 146-stock acceptance through complete automatic
research, saving and reopening. Preserve all 408 proposals and required checks.
Do not repeat unless a failure or relevant subsequent change invalidates it.
Capture non-overlapping stage timings within that run, not separate campaigns.

### 6. Release the exact tested batch

Run affected compatibility and repository-required release checks. Distinguish
known host-suite failures from new regressions; do not claim a failing suite is
green. Package reviewed source/frontend once, verify install/update, retained
configuration/results, restart and saved-result reopening, then ship with one
changelog and rollback route. Public links change only after their matching
artifact exists, under the user's publishing authorization.

A graph refresh, green smoke test or ZIP is not completion. The supported
journey, runtime gate, installable artifact and retained saved results must work.

## Runtime budget and feasibility gate

Targets below are exclusive wall-time buckets, not measurements or promises.

| Bucket | Cached 146-stock automatic journey target |
| --- | ---: |
| Resolve inputs and existing automatic plan | 1 s |
| Local data acquisition checks, preparation and numeric views | 10 s |
| Native simulations for the complete required programme | 30 s |
| Remaining validation, hashing and cross-account checks | 12 s |
| Durable report/result/setup publication | 4 s |
| UI delivery and reopening | 3 s |
| Total | 60 s ceiling; acceptance must be below it |

At the worst-case 512-account ceiling, 30 seconds of simulation requires about
59 ms per account on average; actual account sizes and exact reuse vary. A
four-second individual account cannot be repeated hundreds of times serially
within a minute. This makes compiled, prepared execution a necessary feasibility
candidate. Prefetch or a different parameter picker alone cannot establish it.
If throughput misses, report the remaining gap; do not reduce search/validation
or call a smaller workload a full-run success.

Start the system clock at plugin/import acceptance, including early preparation,
and end at durable result. Report save/reopen and human reading time distinctly.
Report cached versus fresh broker downloads and cold versus warm workers
separately. Broker pacing/coverage remains authoritative; fresh download latency
is not guaranteed by the cached target. Prefetch reduces waiting after Run but
does not erase elapsed work from the complete journey.

## Deferred scope

No new optimiser, score changes, imported universal trading defaults, relaxed
risk limits, GPU rewrite, parallel broker downloader, adaptive sampler
parallelisation, new UI flow or strategy agent. Revisit only a named blocker that
cannot be addressed with installed components. Statistical-method improvements
remain a separate versioned decision; this batch accelerates the frozen policy.
