# Research journey implementation checkpoint

Updated 10 September 2026. This records changes after `0.1.0-preview.4`.
The library and progress increments were installed into an existing OpenAlgo
instance for native testing on 10 September. They are included in the development
source and have not yet been packaged as a new versioned release.
The [delivery plan](DELIVERY_PLAN.md) remains the full scope.

## Source publication checks

The complete Windows research/history suite passed **861 tests, with 42 skipped**
for platform or optional capabilities and one existing warning. The expanded
frontend CI selection passed **148 tests**. TypeScript, scoped Biome/Ruff, source
compatibility and staged whitespace checks passed. Package checks now require
the library and activity modules, and CI includes the new frontend tests.
Runtime databases, credentials, private evidence and local installation receipts
are excluded from the source change.

An initial suite run encountered a Windows directory-rename access error in the
unchanged reference-evidence installer. Its focused tests and a subsequent full
suite run passed without changing that installer; the transient lock's cause
was not identified. No new versioned release artifact is claimed by this push.

## Run progress and automatic continuation (M2.6)

An optimization failed during daily price preparation at the 30-minute download
deadline, before Optuna ran. Its verified acquisition checkpoint retained work.
Automatic time-budget continuation and a compact, animated stage view are now
implemented in the development worktree. The view shows CSV signals/unique
symbols, required versus already available and newly downloaded candles, then
actual Optuna trials, engine preparation and durable result publication.
The [run-progress contract](RUN_PROGRESS.md) records the requested presentation,
counter semantics and recovery checks. These changes are **implemented and
tested**. Controlled browser acceptance passed, followed by installation into an
existing native app on 10 September. The app was idle before its graceful
restart; saved research history, local customizations, configuration and the
existing account were preserved.

Progress verification on 10 September:

- **62 frontend tests passed** across progress, upload sequencing, portfolio
  journeys and library/draft navigation. TypeScript, scoped Biome and production
  build passed. Motion is restricted to the no-reduced-motion media query.
- **61 Optuna/activity tests passed**, including actual terminal trial counts,
  bounded real score history and unchanged result/checkpoint values. Subsequent
  **38 artifact/activity/job/storage checks passed**, including immediate symbol
  transitions, bounded Windows sharing-lock retry and original error preservation.
  These suites overlap; the figures are not additive totals.
- **48 native acquisition/activity checks** and **50 native workflow/policy/daily
  regression checks** passed. They cover durable elapsed-budget continuation,
  cache attribution, old checkpoints, no-data responses and exact price evidence.
- The controlled browser journey showed 9 required daily candles, 2 reused from
  Historify and 7 acquired through the native history/write/readback contract,
  then actual VectorBT/Optuna trials and the saved result. Refresh, library
  navigation and returning to the study recovered its persisted stage/counts.
  Desktop and narrow mobile layouts were checked, including light/dark views and
  no horizontal overflow at a 390 CSS-pixel viewport. Browser screenshots on this
  host have a scaling/cropping limitation; DOM bounds were checked separately.
- A full-cache second study made no broker calls and preserved the exact candle
  values, actual trial records and results. Selected replay preserved its entire
  frozen snapshot. A subsequent web/worker restart preserved authentication,
  all three activity records and original export hashes. Test services were
  stopped, the test port closed and the test worker lease released.

One transient Windows sharing violation during evidence publication was observed.
The writer now retries only Windows sharing/lock errors for at most 1.55 seconds;
disk-full/permission errors still fail, and cleanup cannot hide the original
publication error. The process holding the original lock was not identified.
The resource audit found scoped/closed sessions and files, cleaned-up UI timers
and bounded activity/history. Fresh live-broker acceptance of this increment has
not been claimed from controlled data.

## Working increment

The native Research entry now opens a library. A user can create a named
experiment, add signals, leave and reopen the saved setup, run a backtest,
optimize its settings, replay a selected trial, and return to its study.
Experiments retain their setups, backtests and studies together.

| Journey | Implemented behavior | Remaining work in the complete target |
| --- | --- | --- |
| Capture and organize an idea | Named experiments, notes, tags, pinning, search, archive/restore | Candidate shortlist and decision records |
| Save unfinished work | Account-owned server drafts; a visible save state; flush before in-app navigation and launch | Full last-task/scroll/filter restoration across all planned surfaces |
| Recover a conflicting edit | Optimistic revisions; reload the saved draft or keep the local draft as a separate experiment | Broader offline workflow is not supported |
| Keep and reuse a setup | Immutable named versions; launch freezes a version; restoring/copying preserves the draft it replaces | Composition from a library of independently versioned components |
| Use existing signals | Native upload and account-scoped Sources library; selecting a source starts no calculation or acquisition | Templates, external research import and asset-specific input resolution |
| Run and revisit a backtest | Existing native preparation and VectorBT/optional Nautilus contract; jobs linked to their experiment/version | Expanded performance, exposure, benchmark and trade-diagnostic views |
| Adjust a result | **Adjust & test** restores its recorded settings/source identities into an editable draft with parent evidence | Candidate comparison and broader reserved-period safeguards |
| Optimize a baseline | **Optimize this** leads to the existing Optuna controls; the study remains in the experiment | Native importance/slice/contour/parallel views and the richer study lifecycle |
| Replay a trial | Exact recorded inputs/settings; linked replay; **Back to study** returns to its source study | Return to exact trial filters/axes/zoom, rather than only the study |
| Archive and maintain history | Archived results remain readable/filterable/exportable; launch actions are unavailable; active work must finish or stop before archive | Broader workspace activity and deletion-impact surfaces |
| Reopen older work | Existing saved-job links and the earlier scanner report reader remain available | Optional explicit import/migration into richer future study records |

No new calculation engine, broker downloader or second account system was added.
The library calls the existing portfolio contract and worker. Historify remains
the native market-data archive; frozen research snapshots remain exact run
evidence. Interval selection still follows the entire accepted request, including
timed signals, execution rules and search ranges.

## Where this sits in the plan

- **M0:** daily, minute and mixed reference flows, optimization, exact replay,
  actual worker interruption/recovery, restart and older-report access were
  exercised. Broader asset feasibility and real-broker/host combinations remain
  explicit later acceptance work.
- **M1 core:** library, server drafts, setup versions, safe edits and retention
  are implemented and exercised. M1 is not declared fully complete: Shortlist
  depends on M3 candidate records, and complete context restoration is pending.
- **M2.1:** result-to-adjustment/optimization and same-experiment exact replay
  are implemented. Data/activity drawers, expanded backtest analysis and other
  M2 items remain open.
- **M3–M7 and R:** the planned rich study analysis, continuation, comparison,
  validation/decisions, additional inputs/assets, ongoing review and exact-release
  acceptance have not been marked complete by this increment.

The next delivery sequence stays **finish M2 → native study workspace (M3) →
comparison/validation/decision loop (M4a) → release acceptance** for the existing
cash-equity scope. Additional asset families retain their separate admission
checks; this library work does not enable them implicitly.

## Verification and limits

The browser acceptance used current production React assets, native Flask
authentication/routes, the real calculation worker, VectorBT, Optuna and
controlled candles in native Historify. It exercised upload, save/reopen/refresh,
version restoration, a daily calculation, optimization, selected exact replay,
two browser contexts with conflicting drafts, and archive/restore. The original
backtest export hash stayed unchanged. Desktop and 390-pixel mobile views were
checked; the scripted journey recorded no browser page errors or page overflow.
Additional checks exercised metadata notes search, cancelling/deleting an unused
draft, keyboard focus returning from strategy settings, light/dark study views
and the original legacy report reader. Automated WCAG A/AA checks reported no
violations on the inspected library, setup and study surfaces.
The final restart retained the original session, experiment draft, setup versions,
study/replay links and exact export bytes. Reusing a saved source created a draft
without acquisition or calculation submission.

This was a focused research/authentication harness. It did not exercise the full
OpenAlgo startup, schedulers, live strategies or a real broker connection. The
previous real Fyers daily/minute checks remain historical evidence described in
[STATUS.md](STATUS.md), not new proof for this increment or other brokers.

The final regression counts and release boundaries are recorded in
[STATUS.md](STATUS.md). Development receipts, screenshots, code manifests,
controlled databases and account details are kept under ignored `.agent-native/`.
They are excluded from Git and release assets.

Browser testing found and corrected stale library/experiment reads caused by the
host query-cache policy. A newly created experiment now appears when returning
to the library, and editing waits for a fresh server revision. Navigation and
duplicate launch controls are also held until a job is accepted and linked.

The final backend/storage suite passed **52 tests**; the frontend suite passed
**41 tests**. A Windows backup failure at long temporary paths was reproduced
and corrected. Deep local backup/restore destinations, failed-copy cleanup and
source containment now have regression coverage. This maintenance fix does not
claim arbitrary deep runtime data-directory support or change Windows settings.

The baseline exposed slow first-use engine compilation, including a period at
99% progress. Warm calculations completed much faster. Improving stage reporting
remains part of M2; no estimated completion time is implied by that percentage.

## Implementation entry points

- Native pages: [ResearchLibrary](../../frontend/src/pages/ResearchLibrary.tsx),
  [PortfolioResearch](../../frontend/src/pages/PortfolioResearch.tsx),
  [PortfolioResults](../../frontend/src/components/research/PortfolioResults.tsx).
- Draft lifecycle: [useResearchExperiment](../../frontend/src/hooks/useResearchExperiment.ts)
  and [library API client](../../frontend/src/api/researchLibrary.ts).
- Persistence/admission: [library service](../../services/research_library.py),
  [native routes](../../blueprints/scanner_research.py),
  [metadata](../../database/research_db.py),
  [maintenance integrity](../../services/research_storage.py).
- Regression checks: [library service](../../test/research/test_library.py),
  [navigation](../../frontend/src/pages/ResearchLibrary.test.tsx),
  [draft recovery](../../frontend/src/hooks/useResearchExperiment.test.tsx).
