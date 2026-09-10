# Research run progress and automatic recovery

Status: **implemented, tested and installed for native user testing**. Updated 10 September 2026
after native user testing. Delivery: **M2.6**, ahead of richer study charts;
trial lifecycle and pause semantics connect to **M3.3**. The implementation uses
the existing native price acquisition, worker and React workflow.

## User outcome

The user can tell what the app is doing, how much work remains in the current
stage, and whether useful progress is being saved. The presentation should feel
alive and polished while keeping the healthy path quiet. A single overall
percentage and a spinner do not meet this requirement.

## One compact progress view

Use a horizontal stage track on desktop and a compact vertical version on mobile:
**Read CSV → Prepare prices → Optimize → Results**. A plain backtest uses
**Backtest** in place of **Optimize**. Show one active stage, its meaningful
counters and elapsed time. Completed stages collapse to a short summary;
symbol-by-symbol details and diagnostics belong in the existing contextual
activity/data drawer. Do not add a wall of cards, logs or repeated notifications.

| Stage | Information visible to the user | Counting rule |
| --- | --- | --- |
| Read CSV | Files processed, signals accepted, unique symbols identified; a short action for excluded rows when relevant | CSV signal rows and unique symbols are different units. Deduplicate symbols across all strategies in the portfolio; preserve each strategy's separate signal identity. If parsing offers no incremental events, show an indeterminate reading state followed by real final counts. |
| Prepare prices: check existing data | Automatically selected Daily or 1-minute interval; total required candles; candles already available in OpenAlgo; candles still needed | Derive requirements from the full accepted request, including holding windows and optimization ranges. Count unique required symbol/timestamp pairs; one price candle is not one CSV row. Totals remain provisional until planning and archive checks finish. |
| Prepare prices: download | Newly downloaded and verified candles versus those needed; symbols checked versus total; one active symbol where useful | Count data after native Historify write/readback verification. Keep cache reuse and new downloads separate. A reviewed symbol with missing prices is not a fully covered symbol. Broker omissions/exclusions stay visible in a concise data summary. |
| Optimize | Actual completed trials versus the trial budget, active trial, elapsed time; a small history trace after real trial results exist | Use Optuna's actual states through our adapter. Keep unique calculations, reused proposals, failures and pruned trials distinguishable in details; none may masquerade as a new successful calculation. Prices are prepared once for the study, not per trial. |
| Results | Results saved and ready to open | Complete only after durable publication of the result and its experiment link. Engine initialization and saving results have named states; never sit at a misleading 99% or claim completion early. |

Show an estimate only when there is enough observed throughput to support one;
otherwise elapsed time and current work are sufficient. If every required price
already exists, complete the cache check and proceed without staging a pretend
download animation. The totals and retained progress must survive refresh,
navigation away, browser close and worker restart.

## Animation direction

- Use the existing native shadcn-style components with restrained CSS/SVG motion:
  an illuminated active step, a subtle travelling accent on the connecting line,
  smooth progress transitions and a brief completion check.
- Animate real counter changes and transitions. Motion communicates activity,
  not fabricated throughput. An indeterminate pulse may indicate work with an
  unknown total, but must not increment a percentage or imply completed work.
- In the Optuna stage, the real trial-history trace can grow as results arrive.
  Do not invent sample curves, parameter importance or engine progress events.
- Respect reduced motion with static steps and direct counter updates. Keep
  labels available to screen readers, announce stage changes without announcing
  every candle, preserve contrast and avoid flashing or excessive motion.
- Use one inline issue with an action when intervention is needed. No toast per
  symbol, downloaded batch or trial. Returning to another page keeps a compact
  activity indicator accessible through the experiment/library.

## Automatic continuation is a required reliability fix

Native acceptance exposed a run that reached the 30-minute price-acquisition
deadline and surfaced a generic calculation failure before optimization began.
The existing persisted acquisition checkpoint retained useful work.

Treat elapsed batch-budget exhaustion as a durable continuation, using the
existing bounded worker and queue. Save a consistent checkpoint, release the
current execution segment and continue the same job without requiring manual
Resume for ordinary batching. Maintain broker request limits, queue fairness,
cancellation, worker fencing and immutable admitted prices. Completed requests
(including confirmed empty responses) must not be needlessly downloaded again.

Do not increase the worker limit indefinitely or convert every error to an
automatic retry. Expired broker access, exhausted network retries, incompatible
evidence and storage failures need their own actionable state. Profile archive,
checkpoint and broker-call time separately before choosing performance changes.
Show Pause only once safe pause behavior is implemented; distinguish a pending
pause/cancel request from an acknowledged durable stop.

## Acceptance before marking complete

1. Daily and timed CSVs show correct signal/symbol counts and automatically
   chosen data intervals, including overlapping multi-strategy symbols and
   search ranges that change the required holding window.
2. Fully cached, partially cached and empty-cache runs show truthful candle
   totals and source counts. Downloads use the native broker/Historify path.
3. A simulated elapsed batch limit continues automatically from verified saved
   work; restart and cancellation preserve evidence and do not duplicate requests
   or trials. Broker/no-data/auth/storage cases retain distinct outcomes.
4. No optimization progress appears before price preparation finishes. Real
   trials, repeated proposals, engine initialization and result publication all
   have accurate states; no artificial 99% stall conceals an unknown stage.
5. The complete sequence is understandable on desktop/mobile and with reduced
   motion. Navigation away/back and refresh recover live state without extra
   messages or a new download. Existing saved reports remain unchanged.

## Implementation and verification

Structured events from price acquisition, the coordinator and the Optuna adapter
are persisted separately from immutable calculation evidence. The UI reads those
events; its only timer displays elapsed time. Trial history is bounded to 100
actual score points, while complete trial evidence remains unchanged. Failed,
pruned, reused and uniquely evaluated proposals have separate counters.

The native downloader saves completed archive checks and verified broker
responses before yielding an elapsed or request budget. The same job is queued
behind already waiting work. Confirmed empty responses are retained, and a batch
that saved no progress stops with a retry action instead of looping forever.
External timeouts, authentication, evidence and storage failures are not silently
converted into retries. No database schema or numerical engine policy changed.

Focused checks cover daily/minute requirements, mixed cache/download attribution,
old checkpoint recovery, time limits, empty responses, cancellation and queue
fairness. Optuna checks compare result and checkpoint values with activity enabled
and disabled, including recovery without repeated calculations. Worker checks
verify that results and the completed display state publish atomically. Frontend
checks cover upload sequencing, actual trial counts, engine preparation, status
overrides, unknown legacy counts, accessibility and timer cleanup.

Controlled browser acceptance passed through native price preparation, real
VectorBT/Optuna calculations, full-cache repeat, selected exact replay and
restart. The increment was installed into the user's normal app with existing
configuration and saved history preserved. It is not yet a published release.

The resource audit found context-managed database sessions/receipt files and
bounded event/history data; no new connection pool, thread or global registry
was added. Native browser acceptance and installation are recorded in
[the execution checkpoint](EXECUTION_STATUS.md). Fresh real-broker acceptance
for this increment remains a separate check; private run evidence stays out of Git.
