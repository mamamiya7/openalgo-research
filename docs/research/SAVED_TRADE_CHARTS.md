# Saved trade charts

Implemented in development on 16 September 2026. Backend, frontend and native
browser acceptance pass. Installation and publication are recorded
separately in [STATUS.md](STATUS.md).

## User journey

Open a completed portfolio report, choose **Trades**, then **View on chart** on
an executed trade. The chart opens over the report, showing the saved symbol,
the original candle interval and recorded entry/exit prices. Closing it returns
to the same table, filters and scroll position, with keyboard focus restored to
the action that opened it. Opening or closing a chart creates no research job.

The shared native `OpenAlgoChart` renders the candles and markers. The toolbar
offers visual indicators, chart settings and **Replay**. Review mode permits
temporary chart drawings. No chart drawings, layouts or indicator choices are
saved to the report or restored from another account/report. Entering replay
rebuilds the chart and removes annotations that could reveal a future exit.

Replay advances recorded bars through the native `ReplayController`. Price
inputs and entry/exit markers stop at the current bar; recorded fill details are
shown only when that marker is reached. Playback pauses when the tab is hidden,
and closing the chart stops playback and aborts pending requests. Replay is a
visual review of a known result, not a new calculation or untouched validation.
The existing **Exact replay** action remains a separate numerical-engine run.

## Exact saved-data contract

The authenticated, read-only endpoint is:

```text
GET /scanner-research/api/portfolio/jobs/{job_id}/trades/{ledger_index}/chart
    ?expected_result_artifact={sha256}&period=full|selection|evaluation
    &offset=0&limit=1500
```

The caller must identify a completed, owned native portfolio report and the
exact result artifact displayed on screen. A changed artifact returns a
conflict; another owner's report is inaccessible. No request silently resolves
to a newer report, similarly named strategy, different candidate or another
period. Candidate and condition-test charts read their own report and its
retained input artifact, rather than following a parent to different prices.

`ledger_index` is the zero-based index in the original saved report ledger.
Sorting, filtering and table pagination do not renumber it. An excluded or
skipped signal has no executed trade to chart. A position still open at the end
of the snapshot remains chartable when its entry and quantity were recorded.

Only the report's content-addressed input artifact is read. The source is the
saved `snapshot.bars` on the recorded execution timeline, not today's Historify
archive, the current broker session, raw acquisition extras or an alternative
public price source. Existing artifact integrity checks apply. Opening a chart
does not download, write evidence, recalculate statistics or alter an optimizer.

Responses identify the report, inputs, period and original ledger row. Candle
OHLC numbers are preserved exactly; optional finite volume is returned only if
the saved candle actually contains it. Missing volume is not filled with zero.
The response also retains each candle's original timestamp key and, where
present, its recorded source timestamp.

## Period and execution semantics

Recorded evaluation-basis boundaries govern the displayed period. Older reports
can use their retained coverage and saved selection/later boundaries without
running today's partition algorithm. An earlier report cannot expose candles
from its published later result or reserved evaluation period. A later view
requires an actual published later result; a reservation alone does not permit
chart access. Exact child replays preserve their own saved period identity.

- **Daily candles:** the original date is mapped to UTC midnight solely as the
  chart's display coordinate. This does not claim that execution occurred at
  midnight, nor reconstruct a time within the daily candle.
- **Minute candles:** timezone-aware original timestamps map to epoch seconds.
  A recorded opening fill maps to that observed minute. A close fill recorded
  at the next minute boundary maps back to its preceding observed candle.
- **Intrabar exits:** the recorded execution price is shown without inventing
  the crossing time within the candle. Nautilus OHLC-derived execution events
  remain modelled events, not observed market ticks.
- **Missing observations:** an entry or exit marker without its exact saved
  candle is omitted and identified as unavailable. The service never snaps it
  to a nearby candle, fills a price gap or changes the saved trade price.

## Bounded windows and visual indicators

The selected trade window includes its held interval, plus up to 20 available
saved candles before entry and after exit, within the recorded report period.
An open position can include the remaining recorded report tail. Responses are
bounded to 2,000 candles, with a default page size of 1,500, explicit total count,
offsets and original window indices for entry/exit markers.

Long windows use **Earlier** and **Later** sections rather than downsampling or
silently dropping held bars. Each section has its own bounded **Replay section**
action. It does not claim continuous full-trade playback across unloaded
sections. Changing section discards the previous chart state.

The supplied built-in indicator choices operate on the loaded saved section.
During replay their input stops at the cursor. They do not acquire extra warmup
or read preceding sections automatically. Their values near the section start
can therefore differ from studies with longer history. Volume-dependent choices
are withheld when that section lacks recorded volume. Custom indicator loading
is disabled for this saved-evidence surface. This increment does not admit a
chart indicator into a strategy or Optuna search; causal feature and warmup
contracts remain separate planned work.

The API's response bound is distinct from storage decoding: the existing
integrity reader still allows up to 512 MiB per decoded artifact and reconstructs
that artifact before selecting a page. There is no new candle cache or retained
DataFrame. Selective artifact decoding is a future performance improvement, not
a property claimed by this paged interface.

## Validation and remaining work

Forty-one unique backend checks pass: 27 chart tests, six existing report-contract
checks and eight portfolio-review checks. Coverage includes actual generated
VectorBT daily/minute ledgers, native OHLC/fill preservation, explicit volume,
Nautilus event disclosure, missing/corrupt evidence, a 5,007-candle paged trade,
original ledger indexing, ownership, stale report identities, candidate and
condition-report inputs, open positions and earlier/later period fences.
Repeated chart reads preserve the hashes of all retained artifact files.

The resource audit uses the existing context-managed research sessions and
artifact file reads, with no new client, executor, scheduler or cache in the
request path. Source lint and diff checks pass. These checks do not validate a
broker download or certify economic performance.

Another 133 unique frontend checks pass: 103 cover the research journey, saved
feed and shared chart, and 30 cover Historify and Strategy Chart compatibility.
TypeScript and the production build pass.

Native browser acceptance uses real VectorBT-generated saved reports and the
actual OpenAlgo chart renderer. The daily view has a 40-candle window, with entry
on 22 January and exit on 30 January; the minute view has a 131-candle window,
with entry at 09:46 and exit at 11:16. Original fill markers, EMA/RSI plots,
play/pause, forward/backward steps, seeking and the last replay bar were checked.
Seeking back to the first bar removes future markers and the EMA reading. The
native footer's stale full-result price is hidden during replay. Settings,
cancel and close/focus restoration also pass.

The chart dialog fits the 1,280 px desktop and 390 px narrow viewport. This does
not claim a clean mobile layout for the entire host page, which has existing
horizontal overflow outside the dialog. Browser console checks recorded zero
errors. Request auditing found no broker calls or mutation requests, and all
artifact hashes stayed unchanged. Long-window section paging is covered by the
5,007-candle backend case and frontend journey checks; it was not exercised as a
paged browser fixture. Controlled data validates this interface, not economic
performance or new real-broker acquisition.

Direct **View prices** navigation to mutable Historify, persisted research
annotations, stop/target-level reconstruction and causal indicator admission
to automatic research remain planned. They are not bundled into this first
saved-trade chart implementation.
