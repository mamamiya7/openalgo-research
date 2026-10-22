# OpenAlgo Research verification

## Cached stock preparation — 22 October 2026

Preview.17 groups recovery publication for up to 16 fully cached daily stock
warmup records. Every candle, receipt, calendar and completed record still passes
its existing validation; activity and symbol progress remain current. Before a
broker request and after each response, native acquisition checkpoints still save
immediately, including any preceding cached prefix. An interrupted uncommitted
cache group may repeat at most 16 local stock checks; recorded broker progress
and existing saved evidence remain intact. No new cache, thread or connection.
61 focused checks passed, including legacy checkpoint recovery and exact final
evidence. A short matched real-data preparation comparison (three original stocks
plus NIFTY) took 9.601 →6.237s, 35.0% less; strict scans fell 47 →22. This is not a
full-file, fresh broker download or complete optimisation timing. Features and
search budgets remain frozen; the last complete sample automatic time is still
6m50.091s on preview.15.

A second short pair preseeded only the previous immutable context artifacts
(no database copy), then changed settings equally. Reused-price preparation took
4.433 →2.247s (49.3% less), with strict scans 34 →12. Final evidence and checkpoint
hashes again matched. Each comparison is one before/after pair; neither is a
statistical runtime guarantee.

The comparison uses the actual native stock-market wrapper, existing read-only
Historify/volume records and original 100/160-session calendars. Both paths use
isolated writes, real SQLite metadata reservations and strict read-only ordinary
store quota scans. Final checkpoint hash/counts, native progress callbacks and
full evidence hash match; intermediate cache-only recovery boundaries deliberately
differ (17 →6 outer publications). No broker requests or live archive writes.
A separate 17-stock native test reduced stock-wrapper checkpoints 69 →4, retaining
identical final bundle/checkpoint and all visible symbol counts. Connection counts
balanced after success and interruption. The change retains only a bounded group
counter and small binding tuple; existing scope cleanup handles all resources.
Ruff, formatting and whitespace checks passed. No frontend, dependency, schema,
platform or installer contracts changed; no broad installation matrix was repeated.

Measured alternatives were rejected: one SQL batch saved only about 10ms for 16
windows; batching every existing checkpoint saved about 3% cold and was slower on
repeat loading. Neither prototype is included. The adopted change removes repeated
recovery work rather than reducing data checks or increasing broker pressure.

The comparison with open-source approaches used primary sources:
[VectorBT data implementation](https://github.com/polakowo/vectorbt/blob/master/vectorbt/data/base.py)
iterates over symbols and aligns data; [Nautilus catalog](https://nautilustrader.io/docs/latest/concepts/data/catalog/)
supports bounded batches and identity/date filtering; [Freqtrade history](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/data/history/history_utils.py)
loads cached histories with an explicit time range and startup candles. These
inform the batching/reuse approach; no external source code was copied and no
second price database was introduced.

## Trial bookkeeping correction — 22 October 2026

Preview.16 hashes and publishes the same bounded detached checkpoint tree instead
of copying it twice. Every cancellation callback still reads current ownership
and job state; the owner thread reuses one scoped autocommit connection. Independent
helper/heartbeat connections and atomic pause acknowledgement remain intact.
122 focused checks passed. Matched native-worker continuations produced identical
scientific checkpoint hashes: three late proposal observations took median
3.935 →3.005s (23.6% less); whole short continuation 10.412 →8.647s (16.9% less).
These are warmed short comparisons, not a new complete-run measurement. The last
complete cached real-sample automatic job remains 6m50.091s below. No feature,
search-budget, financial-version or dependency changes; no live orders.

Qualification used the same frozen real CSV evidence, original native VectorBT/
Optuna worker and preview.15 baseline (5899b1a08), isolated writes and read-only
ordinary-store quota scans. Native imports/warm-up were outside both stopwatches.
Late pairs ran before/after/after/before, advancing 242 →245 proposals with the
same six publications and scientific checkpoint hash in all four cases. An early
2 →5 proposal comparison also matched exactly (new-proposal span 2.322 →1.587s).
No complete optimisation or new broker acquisition was repeated.

Checks cover canonical bytes/bounds/fallback, detached snapshot mutation, exact
legacy saved output, publication recovery, attempt/ownership fences, external
cancellation visibility, pause/resume, helper threads and all connection cleanup
paths. There were 118 focused checks plus four existing lease/acquisition checks.
Ruff, formatting and whitespace checks passed. A separate Windows process probe
kept 268 handles and zero lease helper threads after warm-up, 100 and 200 scopes;
RSS settled at 60,248,064 bytes. One hundred unit scopes also balanced 100 engine
checkouts/checkins. No platform/install/dependency/frontend contract changed, so
no broad platform matrix or frontend rebuild was repeated. Existing validated
frontend assets are reused only after source and asset hash checks.

## Complete preview.15 real-sample journey — 22 October 2026

A fresh import in the user's usual Chrome/OpenAlgo installation completed the
same labelled sample as the preceding acceptance: 149 unchanged original CSV
rows, 29 stocks and all 149 original signal dates. SHA verification confirms the
sample and each retained original CSV. Automatic v16 budgets/checks and original
trading assumptions were retained. No shortened search or synthetic candles.

| Measured scope | Actual time |
|---|---:|
| Automatic job, including preparation and saved findings | 410.091s (6m50s) |
| Job creation through first recorded proposal start | 42.463s |
| Exact tested trade report | 4.155s |
| Optuna study analysis | 1.595s |
| CSV confirmation through automatic result saved | 468.378s (7m48s) |
| CSV confirmation through tested report saved | 494.004s (8m14s) |
| CSV confirmation through named reusable setup saved | 561.844s (9m22s) |
| CSV confirmation through verified Chrome reload | 610.242s (10m10s) |

The end-to-end times include operator setup, evidence review and historical-mode
selection. Initial normal-mode submission correctly refused reused final dates
in 1.040s; the previously authorised explicit historical option was then selected.
The successful automatic job ran uninterrupted with one execution, zero replayed
proposals and no code interventions. Progress and saved metrics kept updating;
there was no observed stall requiring a pause. This measures a complete cached
sample journey, not fresh broker acquisition or the full 1,850-row CSV.

The actual programme completed 272 proposals and 296 account calculations, with
294 retained development histories, 27,040 marks and 6,003,034 bytes. Every history
and its frozen mandate verified. All 875 execution candles matched native
Historify OHLC exactly; 29 acquisition receipts verified; no execution candles
were downloaded. The original settings were retained under the existing
historical-only/no-independent-validation label. No final-date claim was made.

Development and later-period financial summaries, proposal/account quantities
and development-history quantities match the preceding complete sample exactly.
The earlier interrupted run reserved 297 attempts for 296 completed accounts;
this uninterrupted run reserved 296. This operational difference changes no
financial result. The earlier 21m17s active observation included interruptions
and mid-run corrections, so it is historical context, not a controlled whole-run
A/B comparison with this 6m50s measurement.

The exact report was saved, a named reusable setup was saved and chosen, and its
result-ready state survived Chrome reload. All 77 prior runs, configuration and
trading activation state were preserved. No live order, mode change, feature
development, installation or new code patch was part of this test. Private
timing, screenshots and verification receipts are retained under ignored
`.agent-native/complete-journey-preview15-20261022/`.

## Validation and replay correction — 22 October 2026

Preview.15 applies the measured opportunities from the runtime attribution below:

- A synchronous portfolio job retains at most one bounded canonical byte
  signature of a successfully validated stock/market bundle. Every lookup encodes
  the complete current content; it never approves a mutable object by identity.
  Changed content is fully readmitted. Plain JSON only is eligible, so tuple/list
  schema differences and custom objects cannot impersonate an admitted value.
  The existing encoder's 32 MiB /524,288-node /64-depth caps bound this optional
  path; larger inputs use the original validators. The first successful admission
  is encoded again before retention to reject mutation during admission. Current
  classifier recipe, outer shape and session checks still run on every call.
  The context-scoped owner clears its byte reference and resets its token in
  `finally`; no approval survives the job. Public calls outside the scope remain
  fully validating, and consumer-specific execution/timing checks still run.
- A seeded fit verifies its previously recorded selection and sizing control
  without republishing unchanged evidence. Missing/new receipts still persist,
  and divergent receipts still reject recovery. Pre-engine attempt reservations,
  newly completed account/proposal publications and live boundaries remain.
- Normal calculation boundaries read lease/job ownership and status together,
  without reserving SQLite's writer. Pausing closes the read session, acquires
  the writer and rechecks fences plus shutdown before acknowledging the pause
  and its study observation in one transaction. Cancellation latency is unchanged.

158 focused unique checks passed: scoped content admission, type/mutation/bounds,
100 success/error owner lifetimes, existing context/preparation contracts,
canonical byte/error equivalence, new/unchanged/divergent recovery receipts,
attempt reservation and construction failures, native pause/resume, injected
between-read/write races and the captured exact legacy result/checkpoint.
Resource audit: sessions remain context-managed; the one-entry byte owner is
bounded and explicitly released on success/failure/nesting, including a copied
context carrying a closed owner. No new file handle, thread or external client.

Matched warmed before/current native-worker comparisons used the same real
acceptance evidence. One early pair added three accounts/proposals; two
alternating late pairs added three proposal observations/two new accounts. All
writes were isolated. Strict quota admission included read-only walks of the
ordinary archive; no broker calls, native archive writes or orders occurred.
Only segment boundaries were timed, without per-function profiling overhead.

| Scope | Before | After |
|---|---:|---:|
| Early whole continuation, one pair | 7.783s | 6.661s |
| Late whole continuation, median of two pairs | 13.785s | 10.141s |
| Late setup/recovery before first new proposal | 9.768s | 6.261s |
| Late three-proposal observation span | 3.982s | 3.851s |
| Late calculation checkpoint callbacks | 12 | 6 |
| Late strict quota invocations | 15 | 12 |

All final scientific checkpoint hashes matched the pre-correction references
exactly. Early continuation was 14.4% shorter and late continuation 26.4% shorter.
Most of the gain is in setup/recovery; this does not establish a new complete-run
time or resolve the remaining growing-checkpoint cost. Search budgets, account
math and validation policy are unchanged. Feature development remains frozen.

An additional root-encoding shortcut was rejected: preserving custom-input
semantics required a plain-tree guard measured at 21.64ms, exceeding the original
18.33ms encoding cost on a real 3.53 MB checkpoint. No such change was retained.
Private comparison/guard receipts are under ignored
`.agent-native/runtime-breakdown-20261022/`. Dependencies, platform/setup contracts
and frontend inputs are unchanged; the full installation matrix was not repeated.

## Current runtime attribution — 22 October 2026

Profiling only; preview.14 calculation and validation code is unchanged. Four
bounded native-worker continuations used the real 149-row /29-stock acceptance
evidence: two early-checkpoint runs (2 →5 proposals, three new accounts) and two
late-checkpoint runs (242 →245 proposals, two new accounts and one already saved
account). Native imports/compilation were warmed first. Metadata and artifact
writes were isolated; quota checks included a read-only walk of the ordinary
archive as well as the isolated archive. No broker calls, credentials, native
database writes or orders were involved. Wall-clock wrappers measured nested
operations; exclusive category times exclude measured children. These timings
include instrumentation and are not fresh-download, fresh-start or complete-job
benchmarks.

| Measured scope | Early checkpoint, median | Late checkpoint, median |
|---|---:|---:|
| Whole bounded continuation | 7.952s | 13.685s |
| Recovery/setup before first new proposal observation | 5.526s | 9.660s |
| First new proposal through bounded worker stop | 2.427s | 4.025s |
| Artifact JSON encoding, exclusive | 0.149s | 1.316s |
| Optimizer fingerprints, exclusive | 0.006s | 0.764s |

Each pair produced identical final scientific checkpoint hashes. The late pair
also matched the earlier before/after reference checkpoint exactly. These short
segments do not establish a new total time for the full optimisation programme;
the previous interrupted 21m17s active sample remains the complete measurement.

Remaining bottlenecks, in order of opportunity:

1. Repeated evidence admission: both early and late workers validated the full
   stock/market bundle nine times and compiled the frozen recipe four times.
   Call paths include context execution checks (twice), execution features,
   portfolio features, recipe/mandate verification, the prepared gate and period
   slicing. Size guards, portfolio fingerprints and bundle validation together
   took about 4.2s of exclusive time per worker. A future correction should share
   a bounded, detached, job-owned validated representation across these consumers
   while retaining each consumer's scope, timing and financial checks; do not
   broadly suppress validators or trust a mutable caller dictionary.
2. Growing checkpoint work: late artifact encoding and optimizer fingerprinting
   together took about 2.08s, versus about 0.16s early. The two scopes have
   different new-account counts; this is a scaling diagnostic, not an account
   speed comparison. The late recovery republished calculation state seven times
   before any new proposal, with three consecutive equal-digest pairs. Completed
   fit selections and sizing controls are verified then republished. Avoiding
   publications when verified scientific state is unchanged is the narrowest
   next correction; preserve new attempt reservations, newly completed evidence,
   failures, pause acknowledgement and changed-state publication.
3. Live worker fencing: the late replay invoked 248 pre-new-proposal pause
   boundaries, taking about 1.14s; the source uses a writer reservation even when
   replaying previously saved proposals. Live cancellation/lease callbacks took
   about 1.02s across recovery and new work. Preserve live checks and atomic pause
   acknowledgement; investigate redundant writer reservations rather than
   increasing cancellation latency.
4. Strict publication quota walks took about 1.21s over 15 invocations late
   (each invocation walks both the isolated and ordinary stores). Reduce redundant
   publications before considering a different quota design. Do not introduce
   stale quota estimates or weaken external-growth/symlink admission.

The two newly calculated late native accounts took 1.10–1.21s inclusive of their
callbacks, about 0.44–0.56s excluding separately timed nested work. Raw account
calculation is therefore not the dominant cost in these segments. The remaining
time includes preparation, copying, study bookkeeping, metadata work and worker
setup/teardown; no claim is made that every non-engine operation is unnecessary.
The read/verification of retained development histories is not the first target.

Private receipts and the bounded profiler are under ignored
`.agent-native/runtime-breakdown-20261022/`. No performance fix, installation or
publication was performed as part of this profiling-only audit. Feature
development remains frozen.

## Remaining repetition profile — 22 October 2026

Preview.14 removes the immediate account-only publication before a new fit
proposal publication. Both now share one checkpoint; immutable account shards
and pre-calculation attempt reservations still publish first. Proposal-construction
failure rolls back partial proposal changes and saves the completed account for
recovery. Reference, fold, final and reconstructed-report checkpoints remain.

A bounded medium JSON path avoids Python token-by-token encoding for admitted
plain trees. It detaches the tree before C encoding, with conservative limits of
32 MiB, 524,288 nodes and depth 64. Caller byte ceilings can only reduce that
allowance. Existing small limits and streaming fallback remain; unusual values,
cycles, nonfinite numbers and size/error precedence preserve prior behavior.
Canonical bytes, hashes, financial versions and validation are unchanged.

Short native-worker comparisons resumed the same real late-search checkpoint:
three more proposals, two new native accounts, 353 seeded evidence files. All
writes used isolated metadata/artifacts. Read-only strict scans of the ordinary
store were included to represent its size; there were no credentials, downloads,
native archive writes or orders. Native imports/compilation were warmed before
both paths. Two alternating pairs without profiling overhead measured:

| Scope | Before | After |
|---|---:|---:|
| Three new proposal observations, including their persistence | 5.029s | 3.942s |
| Whole worker replay, including verification/recovery | 14.963s | 13.553s |
| Checkpoint callbacks | 14 | 12 |
| Strict publication quota checks | 19 | 15 |

The complete final scientific checkpoint was byte-identical in all four runs.
Proposal work was 21.6% shorter; whole replay was 9.4% shorter. These are bounded
continuation timings with isolated writes, not a new whole-CSV or uninterrupted
normal-installation optimisation benchmark. The earlier interrupted 21m17s
sample remains the last complete-journey timing; do not project a new total.

An actual 3.21 MB checkpoint separately retained exact bytes/hash/size: median
size checking was 69.48 →54.51ms and fingerprinting 85.46 →59.89ms over three
repetitions. This is a guard benchmark only. The diagnostic cProfile timings
were not used for speed claims because profiling and cold imports added overhead.

75 focused checks passed, including captured exact legacy result/checkpoint
fingerprints, completed-account recovery without recalculation, injected
construction failures, canonical byte/error bounds and prepared-owner cleanup.
Ruff/format checks pass. No new cache, thread, file handle or external client was
added; temporary detached JSON trees are bounded and released on success/failure.
Feature expansion stays frozen. Host/dependency/installation contracts are
unchanged; the full platform matrix was not repeated.

## Small real-CSV Chrome acceptance — 22 October 2026

A labelled subset of 149 unchanged CSV rows across 29 stocks retained every one
of the original file's 149 signal dates. The ordinary installation completed the
same automatic v16 programme: 272 proposals, 296 account calculations and all
294 development histories retained and verified. All 875 executable daily candles
matched native Historify; no new broker download or live order was made.
Historical-only mode remained explicit. No candidate qualified, so original
settings were retained. The exact report and a named reusable setup were saved
and reopened in Chrome. The 73 prior runs and trading/configuration were preserved.

Optimisation measured **1,276.650 seconds active (21m17s)** and **1,923.699 seconds
elapsed (32m04s)**, including **647.049 seconds paused for two measured fixes**.
The exact reusable report took **9.869 seconds**; Optuna analysis took 2.445 seconds.
These are sample measurements with operator interventions, not an uninterrupted
after-all-fixes benchmark, a fresh-download test or the full 1,850-row CSV runtime.
Runtime remains too long to describe this as a quick consumer run.

Profiling the normal store exposed repeated publication scans after complete
cached reads. Coalescing their redundant checkpoints preserves identical final
outputs/receipts, all partial/broker/interruption saves and strict publication
quotas. It does not cache quota estimates. A controlled fixture reduced 87 saves
to 29 and strict-scan callback time from 23.790 to 9.752 seconds; synthetic OHLC
makes this a persistence benchmark only.

Per-bar progress also called a pause-boundary writer in addition to its existing
live cancellation/ownership read. The duplicate writer was removed from engine
ticks; live reads and periodic heartbeats remain, and pause acknowledgement stays
at completed-calculation boundaries. Six actual-sample native accounts retained
identical reports: 3.839 to 1.094 seconds median with isolated control metadata,
excluding common progress, publication and other search work. A normal concurrent
progress read/save now says “Updating saved progress…”; corruption still fails
closed. No financial version, seed, budget or validation check was reduced.

94 focused checks passed; two platform-specific checks skipped. Resource checks
cover repeated prepared-owner cleanup and live reads; no new cache, client, thread
or open handle was added. Dependency/host/setup contracts are unchanged, so the
full platform matrix was not repeated. Preview.13 packages these narrow fixes.

## Before-search stock preparation — 22 October 2026

Preview.12 targets first-time auxiliary stock history and derived feature work.
It overlaps at most two missing daily stock warm-up windows through the ordinary
native service/limiter, without moving ingestion or evidence publication off the
caller thread. Requests are bounded by the existing budget; returned responses
are processed in order, with exact archive readback and provenance checks. The
job owns and closes its executor. Standalone and single-window calls stay serial.

Read-only native cache acceptance covered all 144 actual stocks and 37,584
warm-up candles in 40.733 seconds, including isolated checkpoint publication.
All OHLC matched saved context. Broker calls, live native writes and provenance
capture were prohibited. This is a warm stock-context measurement, not the first
download, index-context timing or complete app job. The original run's execution
cache check finished in about 20 seconds; its stock/index acquisition continued
to about 704 seconds. No valid cache was purged to force new downloads.

Actual frozen portfolio-feature construction measured 65.515 → 48.151 seconds,
preserving its complete receipt and identity. One-day array/statistic/source
reuse retains ordered correlation operations. Controlled two-response waiting
measured 0.241 → 0.122 seconds with identical responses; live first-use broker
throughput remains unmeasured. Neither component implies a whole-job promise.

113 focused checks passed across acquisition, exact correlation, cache reuse,
request-order failures, budgets, native rate limiting and owned thread cleanup.
Tests verify that a failed first request never admits its already queried sibling,
completed cache requires no credentials, and writes/checkpoints stay ordered.
Financial versions, lookback horizons, seed/search budgets, price intervals and
volume provenance semantics are unchanged. Native broker/Historify core is untouched.

## Optimisation processing correction — 22 October 2026

The preview.11 runtime patch removes repeated admission of one unchanged sealed
portfolio-feature receipt within an automatic run. A private detached copy is
bounded and admitted once per run/resume, then released on every exit. Per-entry
causality, candidate schedules, native fills, cash and portfolio-limit checks still
execute. Public standalone checks continue to verify the full seal. No calculation
version, seed, search budget, financial assumption or evidence identity changed.

Six alternating warm accounts used the actual frozen daily CSV inputs, covering
1,644 development signals across 144 symbols. The median pipeline time fell from
**10.054 to 5.977 seconds** (40.6% less), with **3.126 seconds** of one-time receipt
preparation. All six complete native reports and constraint receipts were exactly
equal. This measures ATR/portfolio attachment, native account calculation and
constraint reconciliation; it excludes condition-gate hashing, history publication,
broker preparation and first-use engine compilation. It is not a new full-search
wall-time claim.

Default saved-artifact reads now use 64 KiB incremental buffers with the unchanged
512 MiB admission bound. In three alternating reads, the 71.6 MB frozen inputs
took **7.632 → 4.236 seconds** median and the 9.77 MB saved report took
**1.233 → 0.570 seconds**, with identical canonical hashes. One compressed leaf's
peak allocation fell from **528 MiB to 1.31 MiB**. SHA, path, legacy manifest,
decoded-size, nesting and cycle guards remain intact; no cache was introduced.

Focused checks: 31 native equivalence/recovery/ownership cases, 116 existing
seal/feature/constraint cases and 34 artifact integrity/resource cases passed.
The allocation fixtures cover daily and minute accounts and reject altered native
fills. Repeated success/error reads retained zero measured bytes and kept Windows
handles at 625; prepared owners are released after 100 success/failure cycles.
Read-only recovery of the real completed study also reproduced every controller
result field exactly, verified all 294 development histories and preserved its
caller-owned checkpoint, without new accounts or artifact/database writes.
The separately qualified historical-mode/progress UI is included in this delivery.
Full Windows/Linux numerical and installation matrices are not repeated: dependency
locks, host calls, launchers and setup procedures are unchanged.

## Broker history and cache acceptance — 22 October 2026

The operator's normal Windows installation was tested through Chrome using an
unchanged real CSV: 1,850 dated signals across 144 NSE stocks. After the operator
reconnected an expired Fyers session, ordinary OpenAlgo history supplied missing
daily and one-minute prices. No orders or trading-mode changes were made.

| Scenario | Native evidence | End-to-end worker time |
|---|---|---|
| Daily, full CSV, one-session maximum hold | 2,932 cached candles; 29 newly admitted Fyers candles in 25 successful broker requests; all 2,961 required candles available | 101.764 s |
| Identical fresh daily job before runtime correction | 2,961 cached candles; 144 archive-read receipts; zero broker receipts; identical OHLC and summary | 37.387 s |
| Intraday, four actual signals dated 19 October, 15-minute maximum hold | Explicit next-session execution on 21 October; 64 newly admitted minute candles in four successful Fyers requests | 8.217 s |
| Identical fresh intraday job | 64 cached minute candles; four archive-read receipts; zero broker receipts; identical OHLC and summary | 1.153 s |
| Daily after correction, first account calculation after app restart | Same 2,961 cached candles, no broker requests, identical results; includes engine import/compilation | 44.552 s |
| Daily after correction, warm calculation process | Same 2,961 cached candles, no broker requests, identical results | 13.885 s |

The CSV contains dates, not intraday observation times. The minute scenario tests
explicit next-session intraday trading from real dated observations; it does not
claim to reconstruct an intraday scanner history. These are fixed-account data
acceptance runs, not a fresh full automatic-search benchmark. Initial daily timing
also includes first-use calculation startup and must not be described as pure
network download time. No cache was deleted to manufacture missing prices.

Every saved executable OHLC was compared against native Historify readback,
receipt hashes verified, and cold/warm summaries matched exactly. All 63 prior
saved results and the environment configuration were preserved. Earlier cached
prices retain unknown original-provider provenance; only the new broker receipts
establish fresh Fyers origin. Other brokers and broader intraday histories were
not qualified by this run. Private CSVs, source/result identities and profiling
receipts stay outside Git under ignored `.agent-native/`.

The active-worker profile exposed repeated full-storage scans inside account
progress callbacks. Portfolio progress now samples quota at most once per ten
seconds per job, while retaining the existing progress/cancellation/worker fence
and strict admission for every new artifact/checkpoint publication. No search,
pricing, execution or result mathematics changed. The correction was installed
and the idle application restarted once for activation. Four new focused cases
cover scan frequency, cancellation, worker replacement and immediate publication
quota rejection. The affected worker/publication suite passed **36 checks with
two platform-specific skips**; lint passed. The correction is included in the preview.10 package; the earlier preview.9 ZIP
remains unchanged. Preview.10 reuses the verified frontend assets after checking
all frontend input hashes. Packaging checks are recorded in its release notes;
this maintenance patch does not repeat the full Windows/Linux matrix.

## Historical preview.4 checkpoint

Checkpoint: 9 September 2026, `0.1.0-preview.4`. **Local acceptance is
complete for the declared native joint-portfolio scope.** Full Docker application
acceptance, cross-version
migrations beyond the targeted calendar update, hosted operation and universal
broker coverage remain unverified.
This checkpoint supports the [public preview source and release](https://github.com/mamamiya7/openalgo-research/releases).
The entries below retain earlier local checkpoints; they are not additive test counts.

## Final preview.4 checks

The full research suite completed with **774 passed, 42 skipped and one existing
Pydantic warning**. The exact preview.4 archive was then installed in a fresh
Windows directory and locked environment: setup, login, the built interface,
VectorBT calculation, Optuna TPE optimization, selected-trial replay, restart,
saved exports and process shutdown passed. Its 40 targeted archive tests passed.
This was a new directory on an existing Windows host with cached packages,
not a clean operating-system image. Test candles were controlled fixtures;
real Fyers daily and minute checks are recorded separately below.

Publication preparation changes frontend dependency pins, documentation, ignore rules, CI triggers and
the supervisor test's source import path. The initial clean Linux CI run exposed
that test path error; its child now imports from the checkout explicitly with
inherited Python paths disabled.
Application and engine sources remain the verified preview.4 revision. Packaging
and compatibility checks passed again (**35 passed, one symlink test skipped**).
The public ZIP is rebuilt from the tagged source and has its own manifest and
checksum; it is not identified by the earlier local candidate's checksum.

The initial public [compatibility run](https://github.com/mamamiya7/openalgo-research/actions/runs/34388052145)
passed 804 Linux and 787 Windows checks, plus 13 Eventlet and 65 Nautilus checks,
the core-to-Nautilus bridge, 102 interface tests, source packaging and the Docker
build and dependency probes. Platform skips and the existing Pydantic warning
remain recorded in that run. Frontend dependency advisories found during packaging
are addressed separately before the final release is published; release notes
identify the final revision's checks.

## Native Fyers no-data correction

Profiling the full saved portfolio separated calculation from acquisition:
VectorBT reproduced the exact result in approximately 6.5 seconds including cold
imports. The fresh run also queried missing windows even though it added no new
candles. Its nine recorded requests included four empty responses and five
unavailable master symbols. Zero new candles does not mean zero broker requests.

One affected window was checked directly through the installed Fyers adapter.
Its response was exactly `s=no_data`, `code=200`, `candles=[]`, with an empty
message. The ordinary adapter had been retrying this successful empty response
three times per window. It now advances without retries for this exact shape,
while genuine errors and malformed responses fail after bounded retries. A failed
later chunk no longer publishes the earlier chunks as a complete success. The
ordinary native service remains the data path; no second downloader was added.

The final Fyers, native history, price acquisition, service and compatibility
targets passed **105 checks**, including 16 new ordinary-adapter regressions.
This is a targeted run after the earlier 758-pass full-suite checkpoint. The
change adds no client, thread, cache or retained resource; existing pooled
transport remains in use and failed in-memory chunks are discarded on return.

## Preview.3 calendar, data and Windows update

The 9 September update uses reviewed NSE annual holidays and special-session
circulars to validate the required native calendar window. Fresh startup repairs
recognized shipped errors; a targeted upgrade does the same on a populated
installation. It does not reset administrator changes or other exchanges. The
old destructive all-exchange reset is no longer an automatic migration.
Unsupported years and unconfirmed required special-session hours fail before
price acquisition. Completed old evidence remains available for exact replay.

On the existing authorized installation, the minute test began with zero rows
for the required window. Fyers returned the historical response, six required
one-minute candles were persisted in the native Historify database, and VectorBT
completed. All six frozen OHLC candles matched Historify exactly. Replay matched
the summary, ledger, curve and contributions; it retained the saved receipt and
made no new acquisition. A receipt inherited by a replay is not a fresh download.

The full 361-signal daily run completed using 1,995 matching Historify candles and
zero newly downloaded candles. Its summary, ledger and strategy results matched
the previous run. The updated calendar removes unnecessary pre-signal padding:
160 curve points instead of 167. The old completed run replay retained its exact
167-point curve. Original CSV hashes, earlier artifacts, account configuration
and the installation's custom market-status component were unchanged.

The full Windows research checkpoint passed **747 tests, 42 skips**. The new
calendar, distribution and cooperative worker-signal targets then passed **37
checks**. The latter overlaps the former; it is not an additional total. CSV-help
UI verification passed **29 PortfolioJourney tests**, TypeScript, scoped Biome,
a production build and desktop/mobile keyboard/download checks. Example CSVs
passed the actual parser's daily/minute selection.

Final review reproduced a shortened special session that could not satisfy a
chosen entry clock. Calendar construction now waits for each strategy's actual
entry and deadline across the full optimizer range, instead of stopping after a
fixed session count. Twenty calendar checks and 52 surrounding checks passed;
the bounded price plan equals the full-calendar plan for skipped special-session
entries, multiday/minute deadlines and mixed portfolios. The final full Windows
research suite then passed **758 tests, 42 skips**, including the new signal tests.
The completion closure retains bounded signal/config/entry metadata only for its
single construction call and owns no connection or long-lived registry.

A preview.2 source ZIP was extracted into a new Windows directory with a new
frozen environment, empty databases and private ports. Setup generated the
installation keys; disposable-account login, a two-strategy VectorBT job, three
real Optuna TPE proposals, selected replay and restart passed using controlled
Historify candles. No owner database or broker credentials were copied. This
proves a fresh directory/environment on an existing Windows host, not a clean OS
image or new real broker acquisition in that environment.

Native terminal Ctrl+C exposed an abrupt worker interruption. The corrected
worker registers cooperative SIGINT/SIGTERM handlers, restores prior handlers
and closes its store even on startup failure. A five-of-six-trial interruption
resumed to the same result as uninterrupted execution. Windows signal/runtime
targets passed **eight checks with ten POSIX skips**; Linux passed **17 with one
Windows skip**. Native app and worker exited zero; eight owned processes were
reaped, all three private service ports closed and the worker lease cleared.

The targeted calendar update applied seven recognized changes on the populated
Windows fixture, then zero changes on repetition. Existing exports remained
byte-identical. A new calculation had the same summary, ledger and strategy
metrics; shared-date equity values matched while the curve shortened from 176
points to its three required-period points. This is an intentional planning
window change, not byte equality between new and old calculations.

Scoped resource review: the calendar helper uses the existing SQLAlchemy scoped
session, rolls back on failure and removes the session in a finally block. The
standalone migration disposes its engine. Repeated native snapshot tests balance
all connection checkouts/checkins. Calendar state is limited to the reviewed
two-year range and each call's bounded calculation window; no long-lived cache,
background thread or connection pool was added. The signal change owns only
local stop state and saved handlers; real shutdown checks cover cleanup.

## Current application acceptance

| Journey | Verified result |
| --- | --- |
| Full VectorBT fixed run | 361 original signals accounted for: 356 eligible and five explicit price exclusions. |
| Full Optuna workflow | 25 TPE proposals and an independent later-period check completed. |
| Exact selected replay | Summary, ledger, equity curve and strategy contributions matched the saved selection. |
| Representative intraday | All 61 required minute OHLC candles matched native Historify. |
| Full Nautilus v2 | Normal native price preparation → Windows worker → WSL calculation completed for 298 eligible signals; 63 exclusions retained. |
| Nautilus outcome accounting | 194 closed, three pending and 101 cash-skipped signals; 57 supplied-grid, one missing-master and five price-coverage exclusions. |
| Nautilus price evidence | All 1,995 frozen daily OHLC rows exactly matched Historify; original CSV/evidence hashes remained unchanged. |
| Consumer interface | Existing-account screen, saved signals, settings, progress, explicit exclusions, results and selected replay exercised. |

The minute calculation path changed from approximately 301 seconds to 6.4 seconds
in one local comparison, with identical financial output. This is an observed
before/after result, not a throughput or capacity guarantee. Private account
performance, source files, credentials and machine paths are not published here.

Nautilus uses `nautilus-portfolio-adapter-v2` and eligibility policy
`nautilus-supplied-grid-windows-v1`. Raw OHLC is checked against supplied metadata
over the full allowed holding window before trials. It is not rounded to force
compatibility. The current master is a supplied specification, not proof of the
historical tick grid. Earlier v1 reports retain their original identity and data.

## Automated checks

| Checkpoint | Result and scope |
| --- | --- |
| Full Windows backend | 742 passed, 38 platform skips. |
| Full Linux backend | 774 passed, 18 skips and one extracted-fixture inventory failure. |
| Linux fixture correction | 20 targeted checks passed after adding the required Git metadata only to the extracted test fixture; no production change. |
| Final Windows changed targets | 38 passed. |
| Focused frontend | 100 passed; final PortfolioJourney rerun 27 passed, with TypeScript validation. |
| Nautilus v2 native engine | 29 real Linux cases passed; accounting and fills are not mocked. |
| Native instrument metadata | Ten cases passed, including unavailable contracts and maximum-window eligibility. |
| Process/runtime boundary | 29 Windows checks passed, including a real WSL cancellation case; the earlier combined Linux engine/lifecycle proof passed 59. |

These are separate, sometimes overlapping runs and are not added together. The
Linux full suite was not represented as rerun in full after the fixture fix.
Coverage includes shared cash, repeated signal lots, native attribution, timing,
Optuna recovery, fixed cohorts, later-period isolation, exact exports, auth/scopes,
native daily/minute preparation and resource cleanup. Controlled broker responses
exercise additional adapters without claiming live verification of every account.

## Fresh installation and populated reinstall

The release inventory and built assets were staged into a new Linux source tree
with a new core environment, empty databases and independent loopback ports.
Frozen core installation, structural compatibility checks, schema initialization
and the locked Nautilus side installer passed. The managed service used Gunicorn
25.3.0 and Eventlet 0.41.2; its private test runner bound the web server to loopback.

The real setup endpoint and built screen loaded. A disposable account completed
setup/login. HTTP submission queued two-strategy VectorBT and real Optuna jobs
against deliberately controlled native Historify candles. Selected replay matched
exactly. The fresh core-to-Nautilus boundary also calculated successfully without
changing the core Arrow version.

After clean shutdown, the same preview source and locked dependencies were
reinstalled over populated isolated data. The account, configuration and exact
exports were preserved. Each shutdown reaped all four owned descendants; no
service ports or owned processes remained. This is an idempotent same-version
reinstall, not proof of a cross-version data migration. No real account or broker
credentials were copied into this test.

The ordinary interactive `app.py` command is distinct from managed Linux service
startup: detached Werkzeug refuses to run. [RUNTIME.md](RUNTIME.md) documents the
verified supervisor prerequisites. The Dockerfile includes a separate locked
Nautilus environment on Bookworm, but Docker was unavailable locally and no image
build/start success is claimed.

## Evidence retention and resource review

Original source and saved-result identities are checked before and after native
runs. Input, snapshot, trial and report artifacts remain immutable. Workflows use
existing account-scoped metadata, Historify and bounded external workers. JSON is
atomic and size-limited; process identities constrain fallback signals; cancellation
reaps a deliberately hung native child even with damaged process metadata. Repeated
real bridge runs retain neither temporary job directories nor growing handle counts.

Machine-specific receipts, logs, raw inputs and source hashes stay in ignored
`.agent-native/` storage. [DISTRIBUTION.md](DISTRIBUTION.md) records the deliverable;
[CONNECTORS.md](CONNECTORS.md) and [NAUTILUS.md](NAUTILUS.md) define supported policies.

## Historical scanner and first-connector checkpoints

The sections below retain earlier verification history. Their then-pending items
and narrower engine scope are historical unless repeated in the current boundary
above. They do not replace the current joint-portfolio acceptance record.

Verified 2026-09-06 after local audit corrections A1–A11. Automated checks use
isolated research stores; the separately authorized main-installation workflow
is recorded below. Original pre-audit receipts and completed reports remain intact.

## Audit acceptance evidence

| Audit | Correction and verification |
| --- | --- |
| A1 | Persistent identity quarantine, explicit dated mappings and boundary gaps. `test_data_audit.py` covers A→B→B, recovery, reviewed transitions, cutoff replay and held-position boundaries. |
| A2 | Eight prior scanner sessions for causal later-window triggers. `test_audit_math.py` compares full-history eligibility across strategies, warmup patterns, gaps/folds and future mutations without requiring warmup OHLC. |
| A3 | Cumulative distinct rows, incumbent, neighborhoods and pass counts; exact parent reports reused. Math tests cover better/worse/tied generations and resume; `test_continuation.py` verifies worker integration and unchanged parent exports. |
| A4 | Canonical categorical strategies; numeric neighbors share strategy and trailing state. Permutations preserve neighborhoods and adaptive selection; mode-only grids cannot imply numeric robustness. |
| A5 | Linked new attempts versus idempotent submission and same-job Resume. Attempt, pipeline and draft/page tests cover terminal recovery, owner scope, strict tokens, completed reuse and denied storage writes/reads. |
| A6 | Raw native Fyers outcomes and duplicates survive the adapter/history boundary. `test_native_history_evidence.py` covers expiry, exhausted retries, empty/partial data, conflicts and cancellation; pipeline tests check independent heartbeat during blocking I/O. |
| A7 | CSV → queued acquisition → expiry/missing-window recovery → checked source → run/export. Pipeline tests invoke real native adapter code with fake network responses. Actual official-extension worker tests use generated later-date archives; data tests reject conflicts and retain old snapshots. |
| A8 | Explicit trailing enabled/value choices, including enabled zero; parameterized math tests cover omitted/explicit axes, include-off choices and replay identity. |
| A9 | Dedicated complete preflight. Component/page tests and exact-width browser checks show all earlier/gap/later assumptions, sensitivity and workload before submission. |
| A10 | Decision/comparison-first reports, human labels and financial units, progressive ledgers and searchable/paginated saved work. Tests and browser checks cover alternatives, exact transfer, filtering and oldest retained export beyond 100 records. |
| A11 | Distinct worker states, joined acquisition heartbeat, real CLI lifecycle and actual app factory/guards/CSRF harness. Supervision, native integration, health and pipeline tests cover recovery and account/broker separation. |

Windows Python 3.12.10: **203 research tests passed in 38.67 seconds**, with the
reviewed public bundle configured. Linux/WSL Python 3.12.14: **208 passed in
57.39 seconds**, including the same suite and five SQLite/eventlet contention
cases; no optional bundle test was skipped. Frontend: **39 tests passed** across
page, draft, preflight, layout and authentication suites. Targeted Ruff/Biome,
TypeScript, Vite production build and whitespace checks passed. Four existing
Fyers `B904` findings outside the changed history code remain; whole-file lint
is not represented as newly clean.

The integration harness imports actual `app.py` and executes its factory,
React/research registration, signed account sessions, global expiry and CSRF.
Unrelated blueprints, database startup, strategy/subscriber threads and live
services are disabled before import. This verifies the native security boundary,
not actual broker login or full trading startup. Worker CLI tests start, observe
health, gracefully stop, restart queued work and verify nonzero startup failure
on invalid storage. No system service was installed.

## Intentional numerical and data differences

Current versions are `scanner-minute-causal-v1` for minute snapshots, `scanner-eod-next-open-marked-v3` for retained daily snapshots,
`scanner-experiments-v2` and `nse-public-evidence-v2`. Older completed results and
exports remain unchanged; incompatible queued/checkpoint work is rejected
explicitly. Later eligibility may differ because causal warmup was corrected.
Search conclusions can differ because all tested parent evidence now survives
follow-ups and categorical ordering no longer changes robustness.

Rebuilding the same generated 18,310-membership scale input with the corrected
importer retained **54,367 bars**, versus 54,691 before. The 324 additional
quarantined records belong to unexplained changed-identity segments: ANGELONE
128, BECTORFOOD 180, GROWWSLVR 1, IVZINGOLD 1, SILVERADD 5 and TDPOWERSYS 9.
Repeated observations do not verify those segments. This is intentional, not
a numerical tolerance change; old immutable inputs remain available.

The importer's seven-session prefix is a separate, explicit pre-CSV zero-count
assumption. An eighth zero session changes no earliest trigger: 1,536 read-only
comparisons across eight strategies produced identical complete ledgers and
summaries for seven versus eight zero-prefix sessions. Later folds need eight
sessions because their prior counts are known, rather than assumed zero.
Unknown historical scanner membership remains a source limitation.

## Representative external-worker run

`tools/benchmark_scanner_research.py` starts an isolated loopback Flask blueprint
host and real external worker, submits through HTTP, requests graceful shutdown
after a durable checkpoint, resumes that job, verifies rows and export, and
stops/reaps its processes. Source hashes before/after the run match.

Windows 11, Python 3.12.10, 16 physical / 24 logical cores, approximately 16 GiB
RAM. The final run shared the machine with other local checks.

| Measurement | Observed |
| --- | --- |
| Generated signals / symbols | 18,310 / 298 |
| Checked official bars / sessions | 54,367 / 193 |
| Evaluated configurations | 3,000 distinct; zero remaining |
| Grid | 20 targets × 10 stops × 15 holds; Bypass, trailing off |
| Checkpoint retained after restart | 30 exact result rows |
| Worker journey including interruption/resume | 541.31 seconds |
| Full submission/work/export journey | 548.84 seconds |
| HTTP submission | 0.278 seconds |
| Health p95 / maximum (934 samples) | 53.81 / 88.04 ms |
| Jobs metadata p95 / maximum (934 samples) | 58.42 / 112.13 ms |
| HTTP failures | 0 |
| Worker peak working set | 296.96 MiB |
| HTTP peak including expanded export | 485.87 MiB |
| Research storage / artifact files | 106.28 MiB / 602 |
| Result compressed / decoded | 4.48 / 52.49 MiB |
| Expanded export | 69.82 MiB; SHA-256 verified |

The exact export SHA-256 is retained in the private verification receipt.
Backup retained three referenced artifacts plus SQLite, **9,021,327 bytes**.
Restore into a new directory verified file/canonical hashes and all metadata
references. Unreferenced old checkpoints remain in the original store.

An earlier corrected run took 355.17 seconds; the pre-audit run took 175.86
seconds with older policy/data. These are observed local timings, not comparable
isolated trials or throughput guarantees. Other trigger/trailing grids have
correctness coverage but are not covered by this timing. Memberships were
generated for load testing, not recovered scanner selections; coverage warnings
remain visible. The benchmark uses disposable authentication and threaded
Flask, not full OpenAlgo/eventlet startup. Peak working set excludes filesystem
cache. JSON export is bounded in memory, explaining its larger HTTP peak.

## Browser and resource verification

Browser journeys exercised public preparation, fixed backtest, search, exact
alternative transfer, disjoint later windows, sensitivity, earlier-only
selection, saved reopening and export. The current-policy two-fold later run
completed 58 evaluations. Comparisons precede long ledgers. A named,
keyboard-focusable ledger region scrolls horizontally with arrow keys.

Exact **320 and 390 CSS-pixel** documents were checked in a same-origin iframe
whose explicit width was measured inside the application document. Desktop zoom
otherwise changes requested outer viewport dimensions. At 320, document
client/scroll widths were both 306 after the scrollbar; at 390 both were 377.
There was no page horizontal overflow; wide ledgers scroll in their own regions.
Material preflight fields and actions remained visible. Native light/dark
appearance and keyboard theme activation were checked without changing trading
mode. This is scoped visual/keyboard QA, not accessibility certification.

The isolated preview catalogue was supplemented with 102 explicitly labelled
generated cancelled metadata fixtures. Through UI pagination, the oldest
retained completed run was reopened and its exact export downloaded without
reupload or recalculation. Search then reopened the current-policy later report.

The resource audit checked NullPool sessions/engines, independent credential-read
sessions, DuckDB, shared bounded HTTP streams, atomic files, backup staging,
subprocess reaping and joined worker-only heartbeat threads. No Flask request
executor or unbounded result cache was introduced. Repeating **200 success and
200 exception** heartbeat/session/directory-scan cycles left handles at **262**
and threads at **7** after warmup; every helper was joined and the temporary
store removed. RSS stayed approximately 54.5–55.2 MiB; traced residual growth
was 2.6 KiB, including measurement records. Separate atomic-receipt checks ran
150 success and 150 failure cycles with 192 handles before/after and no temporary
files left. Blocking cancellation and real subprocess shutdown have dedicated
tests. This is finite repetition evidence, not a hosted soak test.

The pre-audit verification encountered one transient Windows temporary-file
sharing failure; its rerun passed and no cause was established. Current full
Windows/Linux suites pass. Live Fyers downloads, full broker/strategy startup,
production supervision and hosted durable storage remain separate external
checks. See [DATA.md](DATA.md), [DEVELOPMENT.md](DEVELOPMENT.md) and
[STORAGE.md](STORAGE.md) for supported operation and backup scope.
## Release preparation — 2026-09-06

The OpenAlgo release scope is single-owner self-hosting with official-price
research first. See [LAUNCH.md](LAUNCH.md) for the intended-user acceptance tasks.
This pass changes source-selection validation, installation/packaging tools and
documentation; calculation policy and saved numerical evidence are unchanged.

- Windows: **233 research checks passed, two skipped** because the host cannot
  create filesystem links without additional privilege (26.80 seconds).
- Linux: **240 passed, no skips** (50.02 seconds): the same 235 research cases
  plus five SQLite/eventlet cases, including both filesystem-link checks.
- The prior consumer UI pass has **43 frontend tests**. A fresh staged
  `npm ci` / production build also passes through the release packager.
- Missing, blank and invalid source choices create no source, job or artifact;
  explicit synthetic input retains its demo label. Existing explicit sources,
  authentication and CSRF continue to pass.
- The baseline installer copied only catalogued files into a new independent
  directory and verified **201 files / 39,100,537 bytes**. Original manifest
  hashes remain `80c5df101379250db495ae872474535251d2af8cdc22acb66c009f03c50a226d`
  and `8ff52f848ef4e65567f6a7cdf06c0e7b4d452454040f7acb77ed793b443fc51b`.
  Its network adapter is tested with controlled HTTP responses; the local
  provisioning check used the existing public bundle and did not refetch all
  original URLs. Missing/corrupt/oversized/redirect/timeout responses fail cleanly.
- Package tests check source privacy boundaries, exact hashes, current built
  assets, link rejection, repeatable fixture archive bytes, duplicate-output
  refusal and build failure/timeout cleanup. The package contains no live
  environment, runtime data or raw price archives. Source and compiled frontend
  are included together with licence and dependency lockfiles.
- Resource review covered context-managed input/output/HTTP streams, temporary
  staging directories, ZIP handles and waited build subprocesses. Exception and
  timeout tests exercise cleanup. This was a static and failure-path review;
  no new long-lived cache, thread or database session was introduced.

A clean native Linux smoke additionally runs **actual `app.py`, Gunicorn 25.3.0,
eventlet 0.41.2 and one web worker**, with the real native empty-installation
startup. There are no mocked application modules and no preview-auth identity.
All databases/configuration and ports are isolated, broker keys are blank, and
no existing strategy is loaded. Native admin setup, account login without a
broker, anonymous denial, CSRF rejection, current SPA, independent official
baseline, source preparation, external calculation and exact export pass.
Cooperative shutdown releases the lease; restarting the worker completes work
queued while it was offline and preserves the original export bytes.
After every owned process stopped, a consistent backup restored into a new
directory reproduced the same full export SHA-256. All three owned processes
(Gunicorn master and two successive research workers) exited successfully and
the isolated service ports were clear.

The machine-specific smoke receipt and source identities are under ignored
`.agent-native/native-staging/`. This check is loopback HTTP, not a deployed
HTTPS/proxy or systemd/container test. The Docker daemon was unavailable; its
Python lockfile correction was reviewed but no image-build success is claimed.
No external broker validation, public deployment, human trial or data/licence
clearance is implied. Earlier audit and capacity receipts above are preserved.

## Broker-first consumer correction — 2026-09-06

The user rejected the text-heavy interface and clarified that broker history is
the central product requirement. The official-price-first release assumption
above is superseded; its historical checks and candidate are retained as evidence
only. The user's tested run used NSE final CM bhavcopy archives, not Fyers.
No real broker download has been established by any check in this section.

The revised UI defaults new uploads to Fyers without silently selecting an older
public source. Existing reports show their actual provider. Results use
Summary/Trades/Settings tabs, six metrics and an equity chart; trades have six
useful columns, optional row details and 25-row pagination. Saved work moves to a
searchable dialog. Technical run receipts, raw counters and generic edge-case
panels no longer occupy ordinary screens. The exact export still contains the
complete saved evidence. Five main trade inputs lead setup, with advanced controls
and exact preflight available on request. Motion respects reduced-motion settings.

- Windows backend: **252 passed, two filesystem-link permission skips** (22.48s).
- Linux backend plus SQLite/eventlet: **259 passed, no skips** (52.38s).
- Frontend: **44 tests passed** across page, draft, preflight and layout suites;
  targeted Biome, TypeScript and production build pass. The preview build went to
  ignored storage; no tracked compiled assets were replaced.
- Readiness checks cover preview isolation, missing app configuration, absent,
  wrong or revoked broker authentication, symbol-master readiness and reference
  availability. They inspect native metadata without decrypting tokens or making
  broker calls. Ready-to-download means the request prerequisites are present;
  only a successful acquisition can prove data access.
- Resource audit reviewed the two short context-managed SQLAlchemy sessions in
  readiness. Success and exception tests check balanced connection checkout and
  return. No new persistent cache, thread or retained frame was added. This is
  static and failure-path verification, not a long-running production soak.
- Browser evidence captures the actual user's saved report before and after at
  controlled 1,000 and 390 CSS-pixel widths. Checks cover summary/trades/settings,
  arrow-key tab navigation, saved-dialog opening/Escape, source labels and native
  theme switching. These are scoped visual and interaction checks, not an
  accessibility certification. Machine-specific screenshots remain ignored.
- The user's saved export is byte-identical before and after the changes; its
  private receipt records both length and SHA-256. Calculation and data policies
  were not changed in this correction.

A separate real native app and research worker are available for user-owned
account setup and broker sign-in, using fresh isolated databases and ports.
Current frontend delivery, anonymous API denial, setup and worker startup pass.
The instance has no configured Fyers app credentials or broker session. Native
configuration and OAuth are still needed before the mandatory broker acquisition,
Historify persistence and saved broker-result acceptance checks can be completed.

## Native database and shared-broker scope correction — 2026-09-06

The user clarified that the product must use OpenAlgo's price database and its
existing broker integration, not a Fyers-specific research setup. New automatic
sources now read the installation's configured Historify archive first. Complete
stored coverage does not resolve broker credentials or import the symbol master.
Only missing reference-admissible dates call the current native broker. Returned
prices are saved through Historify and must match a read-back before calculation.
The optional stored-only source queues the same validation without downloading.
Development isolation is explicit; normal installations use their normal native
database rather than a mandatory second research archive.

The shared history service preserves richer adapter evidence where implemented
and otherwise accepts the existing native adapter's normalized history table.
Generic evidence explicitly states that raw broker transport was not captured.
No Fyers-only guard remains on new automatic sources, credentials or readiness.
Cached rows lacking original provider metadata remain labelled OpenAlgo Historify,
not the current broker. A retry can fill remaining dates through another broker
while retaining the exact earlier observations and source identities.

- **301 Windows tests passed, two filesystem-link permission skips** (25.76s).
- **308 Linux tests passed, no skips**, including SQLite/eventlet (52.63s).
- **49 frontend tests passed**, plus targeted Biome/Ruff, TypeScript and the
  production build. Setup says OpenAlgo prices; saved public/provider labels are
  retained. The running preview and separate native instance serve current assets.
- Fourteen archive-first tests use real temporary DuckDB storage: cache-only
  operation without credentials, missing-window downloads through three provider
  names, persistence mismatch, partial/empty response recovery, provider switching,
  known quarantine, duplicate-day replacement, bounds and failed-read cleanup.
- Three HTTP worker tests exercise CSV → queued source → real Historify → fixed
  backtest → exact saved export. The complete cache forbids credential/network
  access. Missing-date cases use generic Zerodha/Dhan handlers through the native
  history service, request only the missing date, persist/read back that price,
  and retain byte-identical exports after logout. Controlled responses are explicit.
- Twenty-six shared-history tests cover actual Zerodha normalization with
  controlled transport, eight native feed-token constructor variants, unsupported
  history/intervals, table bounds, cancellation and cleanup. The existing nine
  rich Fyers evidence tests and expiry/retry/restore pipeline continue to pass.
- Code inspection covered all 36 native data adapters. Thirty-one declare daily
  resolution, but Motilal is today-only, some adapters have no history, Tradejini
  is intraday-only, and crypto/sandbox declarations do not prove NSE equity
  availability. None of this is a live-account compatibility certification.
- Resource review covers short native DuckDB contexts, two short readiness SQL
  sessions, atomic receipt files, bounded aggregate cached rows/receipt bytes and
  request-local DataFrames. Failure-path tests verify cleanup. No new background
  thread/client/cache was introduced. Generic native calls retain adapter-owned
  in-flight timeout/retry behavior; checks around them cannot interrupt a hung
  adapter. This is scoped static and test evidence, not a production soak.
- The user's previously tested public-price export remains byte-identical. The
  calculation policy and prior saved evidence have not been changed or relabelled.

The pinned NSE daily identity/calendar/price reference remains a limitation of
current admission; this correction does not extend supported years, venues,
intervals or adjustment assumptions. Public prices never fill a missing native
candle. Real broker-account download and target-host operation are still open
acceptance gates. The isolated native test instance has no user account or broker
credentials configured; its setup, protected API denial and worker lease pass.


## Automatic minute acquisition verification, 2026-09-06

The isolated Windows targeted run passed 31 tests: 13 minute acquisition cases,
four native HTTP-to-worker minute workflows and 14 retained daily cache cases.
The HTTP cases use real temporary native DuckDB storage and fake-network generic
adapters for Fyers, Zerodha and Dhan. They verify cache completion without auth,
one missing minute despite an existing daily candle, exact persisted minute
values, `scanner-minute-causal-v1` saved export and unchanged reopen after broker
credentials are unavailable. The Fyers fixture verifies that a daily-only richer
evidence adapter falls back to the generic minute interface.

Additional cases cover duplicate/non-minute/out-of-daily-range quarantine,
expiry and remaining-data retry, persistence mismatch, missing grid slots,
explicit shortened sessions, receipt quotas and cancellation checkpoints. Ruff
passes for the changed acquisition/helper/test files. No real broker credentials,
network downloads, user databases or live strategies were used. This evidence
proves the controlled native workflow, not every broker's real minute retention
or a live authorized download. Daily reference identity and high/low consistency
are weaker than independently verified minute-tape provenance; that limitation
is retained in saved snapshots. The subsequent full Linux suite passed all 360 tests in 22.55 seconds in a
fresh isolated directory, including native app, supervision and symlink checks.
The owned native Gunicorn/eventlet instance restarted with configuration and all
11 existing database/artifact file signatures preserved. Its setup page returned
200 with account setup still required, protected APIs denied anonymous access,
and its external worker lease was fresh. No account or broker credentials were
created. A first Linux run failed solely because the isolated test runner supplied
a 30-character test pepper; correcting that runner to meet the native 32-character
minimum produced the clean full run. Final consumer asset verification is retained
in the ignored native refresh receipt.

The final frontend passes 55 tests, TypeScript project checking and the production
build. Browser checks at desktop and 390-pixel widths verify the intraday holding
controls and automatic source label. Obsolete daily-only setup narration was removed.
The automatic-preparation journey test changes the holding rules, receives a queued
preparation, switches to the completed minute source and resumes review without a
second upload. Seven backend admission/worker tests cover source reuse, wider
requirements, temporal search/sensitivity variants, minute checkpoint policy and
unchanged parent artifacts. The original saved export remains unchanged, with its
exact hash retained in the private verification receipt.

Resource audit for this correction was static review plus failure-path tests:
native DuckDB contexts close on success/error, receipt files use context managers,
generic history handlers close in `finally`, worker lease helpers are joined, and
chart instances are destroyed. Minute plans, rows, receipts and per-evaluation
state have explicit bounds. No new global cache or long-lived client was added;
this is not a production memory/descriptor soak or minute-scale capacity benchmark.

## Full main-app workflow verification, 2026-09-06

The owner authorized testing the existing installation and connected Fyers
account in place. The original date-only CSV was reused, with 361 signals across
298 symbols. The automatic planner kept daily prices for the ordinary backtest
and all daily search variants. Expanding holding periods from the original setup
to the full search increased required scope from 2,000 to 2,637 daily candles.
Existing matching-interval Historify rows were reused; only missing scope went
through OpenAlgo's ordinary connected-broker history service into that same
database. The completed expanded snapshot contains 2,606 native candles with
31 missing. Every calculation OHLC matches native storage exactly, with no extra
public-price adjustment. Original saved inputs and results remain unchanged.

| Main-installation workflow | Verified outcome |
| --- | --- |
| Original fixed daily repeat | Exact result equality with the previously completed original run; 200 closed, 149 skipped, 12 pending |
| Full optimizer | 648 distinct settings evaluated, zero remaining |
| Sensitivity | Double fees, higher slippage and reverse signal priority completed |
| Fixed later-period holdout | One later window and three sensitivity variants completed |
| Earlier selection plus walk-forward | Two later windows, 648 earlier settings per window and three sensitivity variants per window completed |
| Exact selected-setting rerun | Submitted through the real browser; configuration and entire result equal the saved optimizer selection |
| Representative intraday | One actual signal with an explicit intraday horizon automatically used 375 cached native minute candles; one closed trade, no missing prices |

The intraday run is a one-signal representative check, not a full intraday replay
of the 361-signal CSV or a live minute-download benchmark. Fyers is the actual
connected broker exercised here; other brokers remain controlled-adapter tests.
Missing daily candles retain pending outcomes and do not become fabricated
prices. The historical walk-forward checks used fresh capital for each window
and retained their original reports; private account performance is not reproduced
in these public verification notes.

Chrome checks covered automatic expansion/preparation, full optimizer submission,
selected-setting transfer and submission, saved-run search, reopening after a
full reload, trade filtering, saved settings, rendered charts and switching later
windows. All saved export payloads were serialized and hashed again from their
immutable inputs and reports with exact equality. The browser Export results
link was exercised, but final file arrival was not confirmed: browser policy
blocked download-manager inspection. This is a limit of that manual check,
not evidence of a failed export endpoint. Automated export/HTTP tests passed.

Full automated validation on the current source:

- Windows: **489 backend checks passed, two symbolic-link permission skips**,
  71.34 seconds; entire research suite plus shared history routing.
- Local Ubuntu/WSL: **504 backend checks passed, no skips**, 88.67 seconds;
  the same scope plus 13 SQLite/eventlet checks. Both Windows-skipped cases passed.
- Frontend: **1,478 tests across 75 files passed**, 27.54 seconds; TypeScript,
  targeted research/chart lint and production build also passed.

Automated runs used isolated temporary stores and existing local dependencies;
no new dependencies, infrastructure or system services were provisioned. No
application source change was needed during this full verification. Main-app
jobs used the existing external worker; no orders, strategy activation or mode
changes were performed. Exact job identities, source/export hashes and complete
runner logs are retained only under ignored `.agent-native/full-workflow/`.
The main app remains running on its existing local address.

Browser follow-up found a presentation issue in the representative intraday
report: its saved equity timeline contains 61,125 minute marks through the
snapshot cutoff although the one trade closed in January. The chart opens on the
latest September marks, showing a flat tail instead of the January trade. The
375 required price candles and saved calculation are correct, but the initial
chart viewport and unnecessary report size need a separate presentation fix.
The first walk-forward window also repeats its insufficient-sample label. These
findings prevent treating this run as a completed public-UI acceptance review.
A repository-wide whitespace check reported existing generated
`frontend/dist/index.html` line-ending whitespace; generated assets were not
rewritten during this test pass.

## Available-price report completion, 2026-09-07

The owner asked to proceed with the prices already present. Existing daily
backtest and optimizer results remain completed with the exact same native data
and pending outcomes; no new broker requests or calculation jobs were created.
A read-only recheck confirmed all nine prior jobs, exact baseline and optimizer
selection equality, and every daily/minute calculation price against Historify.

Settled minute reports now initially focus their chart on the actual entry/exit
span, including the preceding equity mark. This changes the viewport only:
the complete saved series, numerical summaries and exports are unchanged and
remain available. Pending outcomes and later-period reports retain their complete
review horizon. The recorded large minute export is deliberately unchanged.
Minute evidence rows use their timestamp identity instead of the repeated date;
the later-period view no longer repeats a sample finding already shown inside
the report.

The targeted three-file frontend suite passed 53 tests before the final
timestamp-key correction. The final research-page suite passed all 44 tests
without duplicate-key warnings; TypeScript and targeted lint passed. The main
installation's own production build passed, preserving its custom market-status
hook and environment hashes. Only the two reviewed UI source files were replaced;
source backups, the previous index, build log and receipt remain ignored under
the main installation's `.agent-native/available-prices-ui/2026-09-07/`.
Older asset files remain available for open tabs and rollback.

Resource audit: static review of the chart effect and its bounded viewport
object. The existing destroy-on-rerender/unmount cleanup still releases chart
subscriptions and canvas resources; no new listener, timer, client or cache is
introduced. No resource growth was suspected or measured. Chrome's normal login
session expired overnight, so this update has not yet been visually confirmed
inside the authenticated main-app report; no authentication bypass was used.

## Modular connector workflow, 2026-09-09

Real optional packages were installed only in disposable development/test Python
environments: VectorBT 0.28.5, Optuna 5.0.0, with existing core dependency versions
constrained. The source patch adds the locked `research` extra. No main account,
broker credentials, live archive, scheduled strategy or main process was changed.

Verification covered:

- 18 real VectorBT numerical cases: next-open timing, opening-equity sizing,
  shared cash, same-symbol lots, fees/slippage, protective orders, pending outcomes,
  sparse flat periods and early input bounds. The first native compilation needs
  a larger timeout; this allowance is scoped to the connector tests.
- 28 real Optuna cases: finite-grid parity, distinct bounded trials, composite
  trigger/trailing choices, deterministic restart, input/version binding, damaged
  evidence, cancellation and invalid scores. The optimizer receives the same
  immutable snapshot through the supplied engine callback.
- Native upload -> preflight -> queue -> real calculation -> reopen -> exact
  export for VectorBT fixed runs and Optuna with both VectorBT and the existing
  scanner. Additional checks cover owner access, unavailable packages, version
  drift and worker interruption/resume without repeating completed trials.
- Daily acquisition tests use real temporary Historify storage and controlled
  broker responses. Both engines reuse complete daily coverage without credentials;
  missing-only daily downloads persist and read back through the existing native
  service even with minute candles already present. No new live-broker download
  is claimed by these controlled checks.

The full Windows run recorded 548 passes, two existing symbolic-link permission
skips, and one incorrect new test assertion expecting HTTP 202 instead of the
existing Resume endpoint's HTTP 200. Linux recorded 563 passes and the same
assertion failure, with no skips; this includes 13 SQLite/eventlet checks. After
correcting only that assertion, its complete interruption/resume scenario passed
on both platforms. The verified case totals are therefore 549 Windows cases and
564 Linux cases, with the two Windows-only skips. The original full-run logs and
corrected Linux rerun remain under ignored `.agent-native/connector-work/`.

All 60 affected frontend tests passed, including supported controls, independent
optimizer choice, preserving strategy requirements, combined package availability
and retaining engine version pins for exact candidate reruns. TypeScript, scoped
Biome/Ruff checks, frozen-lock consistency and the production frontend build
passed. The build is under ignored `.agent-native/connector-work/frontend`; the
tracked distribution and main installation were not replaced. Browser visual
verification of the new controls in the authenticated main app is still pending.

Resource audit: static review found a fixed-size adapter registry, bounded
500,000-cell engine matrices, bounded serial Optuna studies and one retained full
winner report. New code opens no broker client or separate database. Persistence
uses the existing context-managed sessions and atomic/chunked artifact writer.
The worker's lease helper is joined on success, failure and interruption; lifecycle
tests assert its thread is gone after both failure and resume. Cancellation cannot
interrupt an upstream import/compilation immediately, but prevents publication
after it returns. No sustained descriptor/RSS benchmark or production-speed claim
was made. That early checkpoint preceded the joint portfolio, Nautilus and
later-period implementation verified above. Margin remains outside the declared
connector scope.

## Main-installation connector test, 2026-09-09

The owner asked to open and test the feature in the existing app. Only connector
source, optional dependency declarations and the main app's own rebuilt frontend
were applied, with reversible backups under its ignored `.agent-native/` directory.
All previously installed package versions, `.env`, the custom market-status hook
and the original full historical result were verified unchanged. Existing strategy
and workflow schedules were empty before restarting the owned web/worker launcher.

Five actual scanner observations with complete stored holding windows were selected
from the original upload for a small functionality test. The normal native price
preparation completed from 40 stored daily candles; every OHLC value matched native
Historify, all acquisition receipts were archive reads, and no new broker requests
were needed. The ordinary worker completed VectorBT with five accepted/closed
trades and zero pending/skipped, and VectorBT/Optuna completed 25 distinct settings
from an 81-setting grid. This is a workflow check on a selected small sample, not
strategy-performance evidence or a new live-broker download test. Exact private
receipts and the reusable CSV remain inside the owner's installation.

The main installation passed all 60 affected frontend tests and its TypeScript /
production build. Chrome showed the existing authenticated account, saved test
source and Calculation tools control. The app remains available on its usual
loopback port 5000. Broader connector numerical, recovery and Linux checks remain
as recorded above; they were not repeated solely for this installation change.
# Distribution and connector defaults — 2026-09-09

This increment packages the first connector workflow as one native OpenAlgo
Research distribution. It does not add new simulation scope or change either
engine's financial policy.

- **69 affected frontend checks passed**, including new VectorBT/Optuna defaults,
  unavailable-engine blocking, fresh New run behavior, legacy draft recovery and
  exact saved-candidate settings. TypeScript and targeted Biome checks passed;
  the final small connector label adjustment also passed its ten focused tests.
- **31 distribution/compatibility checks passed on Windows**, with one symlink
  test skipped because the host does not permit creating those links. Checks
  include a missing native price adapter, incompatible host call signature,
  unsupported package/Python versions, archive hashes, source mutation during
  build, private-data exclusions and subprocess failure cleanup.
- **11 production supervisor checks passed on Linux**: signal shutdown with a
  checkpoint, startup failure, child crash/exit, stubborn children, descendant
  cleanup and 20 restart/exit cycles with unchanged descriptor and child counts.
  Windows passed the command contract; ten POSIX lifecycle tests skip there.
- The real source builder ran locked `npm ci` and a TypeScript/production frontend
  build. The extracted ZIP passed its source and installed-package compatibility
  check and **70 real calculation/API/native-data workflow tests**, covering
  VectorBT, Optuna, cancellation/checkpoint recovery and daily Historify behavior.
  All shipped file hashes matched the release manifest. These tests used isolated
  temporary databases and controlled broker responses, not an owner's account.
- Targeted Ruff, shell syntax, Compose configuration and whitespace checks passed.
  The added CI workflow specifies both supported operating systems and an image
  build/dependency check; it has not run remotely in this local work.

The actual Docker image build/start, first account setup and populated-volume
update/restore are **not verified here**: Docker Desktop's Linux daemon was not
running. No live app, broker, credentials or account database was used. The owner's
running main installation was not modified by this increment. No public repository
was created and nothing was deployed.

Resource audit: the checker uses short, closed file reads and no app/database or
numerical imports. Packaging owns and closes staged files and archives, reaps
build children and cleans staging on failure. The supervisor retains at most three
child handles, restores signal handlers and removes its temporary stop-file
directory on all ordinary exit paths; Docker's tini reaps orphan descendants.
File ownership was reviewed statically; process cleanup was also measured in the
Linux repeated-cycle test.

Local receipts: `.agent-native/distribution-verification/receipt.json`,
`packaged-workflow.xml` and `check-*.log`; release source/asset manifests and build
logs live under `.agent-native/release/`. These are ignored development evidence.
