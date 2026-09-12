# OpenAlgo Research status

Updated: 12 September 2026. Published preview: **`0.1.0-preview.4`**, targeting OpenAlgo 2.0.2.2.

## Current development increment

The [consolidated backlog and delivery sequence](DELIVERY_PLAN.md#9-execution-order-and-completion-reporting)
reconciles all 34 journey entries with the library, progress, Chartink and analytics
increments. It separates the next report/study work, first complete cash-equity
release, professional research depth, asset admission and optional connectors.
The first dependency-ordered implementation is now in the worktree: stable
report/configuration/period identities; an optional **Reserve a later period**
setting for baseline or search; explicit frozen-input evaluation linked back to
the chosen study/trial; a continuous Report page; and version-2 risk analytics.
Reserved dates are frozen before price acquisition. The initial run evaluates
only the selection period; later evaluation is an explicit new linked run.
Replay and another replay retain the original candidate identity. Original
summary/objective/export evidence is preserved.

The new Report places performance and underwater charts beside grouped statistics,
then consistency, rolling risk, drawdown episodes and trade/capital sections.
Lower charts mount near the viewport. Advanced statistics remain collapsed.
Daily return quantiles and 21/63/126-session Sharpe, volatility and Sortino use
complete account observations. Analysis v2 deliberately treats a nonpositive
previous account equity as an undefined percentage return, rather than silently
dropping that observation; original summary/ranking values do not change.
Older analysis remains readable and has an explicit **Update report** action.

The next report slice is now implemented: **Customize** saves headline/side
statistics, view/log scale, rolling window and expanded sections with the account.
Stale tabs cannot overwrite newer preferences. Month cells and drawdown rows open
saved-window charts and actual trades in a dialog; Settings also opens in place.
New evaluations record their exact ordered signals, admitted cohort, timestamps,
prices, calendar and instrument basis, with capital/cost/engine context kept
separate. This supports future fair comparison; an old result without that
descriptor remains unverified. No saved financial values are rewritten.

Verification includes real VectorBT/Optuna calculations with controlled frozen
prices, reserved baseline and candidate evaluation without another acquisition,
old-analysis upgrade, populated library backup/restore and release inventory
checks. Frontend interaction tests, TypeScript, scoped lint and production build
passed. Chromium checks covered light/dark desktop, 390px mobile, chart expansion,
focus restoration, rolling-window changes and trial settings; no page exceptions
or horizontal page overflow were observed. See the exact
[first receipt](REPORT_EXPERIENCE_PLAN.md#11-first-implementation-receipt--11-september-2026)
and [next-slice receipt and remaining work](REPORT_EXPERIENCE_PLAN.md#12-preferences-investigation-and-evaluation-basis--11-september-2026).
The latest slice passed 74 frontend checks, 41 preference API/storage checks,
22 focused evaluation-basis/contract checks and 7 existing period regressions.
Distribution/package checks passed 38 with one environment skip. A fresh-browser
test verified preferences, inline and expanded heatmap investigation, settings
and drawdown focus restoration, mobile layout and unchanged original export.
The report increments were installed into the existing app on 11 September,
after confirming zero active calculations. The existing supervisor restarted
the app and worker; all 56 prior research job records and all pre-existing
research-table fingerprints were preserved. Configuration, broker adapters and
local frontend customizations stayed unchanged. The new report build is served,
the preference table exists, authentication is enforced and the worker is online.
Automated Chrome interaction stopped because the browser address could not be
reliably identified; no fresh signed-in click-through is claimed. Optional
Nautilus acceptance and a versioned release of these increments remain pending.

The next R4 slice is now implemented and observed in the controlled browser:
**Study Overview / Parameters / Trials / Activity**, linked native observations,
sortable scalar columns, distinct portfolios versus actual proposals, and exact
candidate reports. The best report opens immediately; other candidates prepare
once from retained prices/settings through the bounded worker. Original summaries,
native statistics and evaluation basis are checked before publication of a child
report. Study exports and ranking do not change. Reports are library-linked and
survive restart/backup. Candidate admission/resume respects archive state.
Study section, table scope/sort/page, chosen parameters and selected trial return
with the user; the best report has its own URL state and browser-history entry.
Library job refresh preserves unsaved setup edits and conflict state.
See [the R4 receipt and precise limits](REPORT_EXPERIENCE_PLAN.md#14-connected-study-and-exact-candidate-reports--12-september-2026).

The next R4 slice now records durable **trial activity** separately from scientific
checkpoints. New executions retain actual proposed settings, outcomes, failures,
cancellations and interruptions. Recovery preserves the prior attempt; replayed
proposals do not appear as fresh calculations. A worker lost without a recorded
finish retains an unknown end time. The Activity view has bounded pages, attempt
filtering and in-place settings; active or failed runs open it on demand. Original
result exports, scores and seeded recovery remain unchanged. Native failure/resume,
storage recovery and controlled desktop/mobile browser checks passed; see
[the activity receipt](REPORT_EXPERIENCE_PLAN.md#16-durable-study-activity--12-september-2026).
The first R5 slice now adds a saved **Shortlist** to each experiment. Save an actual
study trial or fixed backtest, rename it, add a note and return to its exact settings
and report. Native metadata preserves its original result/configuration/period and
first saved proposal; duplicate saves reuse the same candidate. A full-period
baseline stays distinct from a selection-period trial. Saving does not calculate,
download, change the winning trial or invalidate the setup draft. Report preparation
remains explicit. Archive viewing, bounded pages, stale-edit protection and
backup/restore are covered. See the [shortlist receipt](REPORT_EXPERIENCE_PLAN.md#17-exact-saved-shortlists--12-september-2026)
for 90 backend and 77 frontend checks and the controlled native desktop/mobile journey.
The activity and shortlist slices were installed into the existing app on
12 September: 21 runtime files and the native frontend build, with all 57 saved
jobs and every pre-existing research-table fingerprint preserved. The three new
activity/shortlist tables start empty, the worker is online, and authentication
and CSRF checks passed. Configuration, broker/history data and local UI
customizations remain intact. See the [installation receipt](REPORT_EXPERIENCE_PLAN.md#18-activity-and-shortlist-installation--12-september-2026).

**Next work:** R5 fair comparison and decisions on the now-saved candidate/report
identities, with evidence-use history. R3 independently frozen benchmark
evidence can proceed separately. Full reference-field mapping, real optional
Nautilus acceptance and broader normal-account journey acceptance remain open.
The [report experience specification](REPORT_EXPERIENCE_PLAN.md)
defines a continuous tear sheet, connected Optuna study dashboard, candidate reports,
benchmark comparison, shortlist and saved validation/decisions. It records exact
page contents, score/count semantics, data ownership and R0–R6 acceptance. The
optional QuantStats connector is pinned there as **QS-01**, to evaluate before
choosing the final report export implementation. R0–R2 have the bounded
implementation receipts above; they are not claimed fully accepted. R4's connected
study/candidate and durable-activity slices, and R5 saved shortlists, are
journey-observed on controlled data; broader acceptance and the full R3/R5/R6
packages remain open. On 12 September,
the authorized 20-file study update was
built against and installed into the existing app. All 57 saved jobs and every
pre-existing research-table fingerprint were preserved; the candidate table was
added, the app/worker restarted, the current bundle is served, and native login/
CSRF protection was verified. Configuration and local UI customizations remain
unchanged. This is installation smoke acceptance alongside the controlled browser
journey, not a new normal-account calculation. Source publication includes the
complete prebuilt UI; the packaged release remains preview.4.

The full [tear-sheet increment](TEARSHEET_PLAN.md) is implemented: 121 VectorBT
and 88 Nautilus catalog entries, extended scalar metrics saved per distinct new
trial, native Optuna plots, and frozen
OHLC charts with recorded fills. Metric definitions distinguish daily marked
account returns from native closed-position returns. Unsupported benchmarks and
undefined samples retain explicit nulls. Older studies can prepare separate
versioned analysis artifacts without broker downloads or changes to the original
result. Its former Summary/Tear sheet views are now combined in Report in this
worktree. Native testing and installation are recorded in the plan's acceptance
section; this increment has not been published as a versioned public release.

Optimization results open each trial's settings in a dialog and offer a
compact **Columns** chooser. Win rate and profit factor join the default table;
the chooser exposes the 15 saved summary fields and separate objective score
where present. Preferences stay account-scoped in this browser, table headers
remain visible while scrolling, and trial pagination survives result-tab changes.
Existing studies can still use their original fields immediately; see the
[metric inventory](TRIAL_RESULTS.md). The preceding table-only increment passed 42 component/journey checks,
6 library checks, TypeScript, scoped Biome and the native production build passed.
The three frontend files were installed into the user's normal application on
11 September without restarting its app or worker. Chrome checks on the existing
saved study confirmed the actual metrics, column persistence and settings-dialog
focus restoration. Original results, backend calculations and data were unchanged.

Chrome connector **0.1.1** now has an original logo, Chrome icons, popup Privacy/Help
links, a bundled policy, license/notices and a reproducible Web Store ZIP with its
manifest at the root. Optional HTTP access is limited to loopback; remote hosts
require HTTPS. Verification: 26 extension checks and 6 package checks passed;
one private-export fixture check was skipped. Artwork was visually inspected.
The user reports the unpacked extension is working; the new 0.1.1 build still
needs a fresh-profile manual check and real store screenshots. The
[publishing guide](CHROME_EXTENSION_PUBLISHING.md) includes store copy, data
disclosures and the specific Chartink export-terms question. No publisher account,
public source release or Web Store submission was created in this preparation.
A compatible versioned app release with import protocol 1 remains necessary.
The extension source, policy and preparation documents accompany the current
source update; this does not submit the extension to the Chrome Web Store.

The first [Chartink connector](../../extensions/chartink/README.md) increment is
implemented in this worktree: scoped Chrome pairing/capture, native authenticated
import, a saved named experiment, original CSV/source evidence, duplicate-safe
retry, related experiments for changed history, and suggested optimization ranges.
Real VectorBT/Optuna integration passed using isolated native daily Historify data;
the audited official export also opens in the native setup with its 365 signals
and 111 symbols. On 11 September, the matching source and a frontend built against
the existing installation were installed into the user's normal app, followed by
a verified restart. Existing research records, configuration and customizations
were preserved, and the worker returned online. Chrome's browser security policy blocked
automated extension installation, so the installed-extension click-through remains
a manual acceptance step. The full [journey](CHARTINK_JOURNEY.md) is still broader
than this increment; presets, rich study exploration, shortlists and saved
comparisons are not claimed complete.

The native Research library, server-saved experiment drafts, setup versions,
revision conflict recovery, archive/restore and result-to-setup/study/replay
transitions are implemented in the development worktree. The integrated browser
journey passed with actual VectorBT/Optuna calculations and controlled native
Historify prices. The increment was installed into the user's existing instance
for native testing on 10 September; it is included in the development source and
has not yet been packaged as a new versioned release.
Native testing exposed a price-download deadline failure before optimization.
Automatic download continuation and compact animated stage/counter reporting
are now implemented, specified in [run progress](RUN_PROGRESS.md). The progress
increment passed controlled browser acceptance and was installed into the user's
normal app on 10 September. Existing configuration and saved runs were preserved;
no active job was interrupted. The latest packaged release remains `0.1.0-preview.4`.
On 11 September, progress was refined to show accepted signal dates, the exact
unique candle requirement before archive checks finish, and a compact holding-window
explanation. It was installed and checked on the active native run without a
restart; no data acquisition or calculation behavior changed.
Daily price preparation now groups bounded archive reads and nearby missing
requests, initializes Historify once per acquisition scope and reduces repeated
artifact storage scans. Tests preserve required prices, cached candles, minute
behavior, cancellation and old checkpoint recovery. Controlled local acquisition
timing improved from 19.51s to 3.53s for a synthetic 16-symbol case; this is not a
live broker speed guarantee. The five-file research update was installed into the
normal app on 11 September after the active backtest completed. Restart checks
confirmed the worker online and all prior research records, broker code,
configuration and frontend build contents preserved. Native startup regenerated
compressed asset copies, verified against their unchanged original contents.
See [download performance](DOWNLOAD_PERFORMANCE.md) for the scope and limits.
Calendar exclusions now show the affected signal date and the saved calendar
range, distinguishing dates before/after that range from missing sessions within
it. Complete saved curves also supply neighboring session dates. This display
update was installed and checked in Chrome on 11 September; it clarifies existing
reports without changing stored exports, prices or calculations. Targeted checks:
19 result/HTTP tests and 18 packaging tests passed, with one Windows skip.
See the [implementation checkpoint](EXECUTION_STATUS.md)
for working journeys, remaining M1/M2 work and verification boundaries.

Frontend verification for the library increment: **41 tests passed** across the existing
portfolio journey checks, draft persistence/recovery and new library navigation;
TypeScript, scoped Biome and a production build passed. Browser checks also
covered light/dark layouts, keyboard focus restoration, deletion cancellation,
notes search and old-report access. Automated accessibility checks reported no
WCAG A/AA violations on the checked library, setup and study surfaces; this is
bounded coverage, not a whole-application accessibility certification.

The subsequent progress increment passed **62 frontend tests**, native acquisition
and Optuna regressions, controlled price preparation, a cache-only repeat,
selected exact replay and restart checks. Detailed counts and scope are recorded
in the [implementation checkpoint](EXECUTION_STATUS.md).

Backend verification: **52 library/storage tests passed**, including real
Optuna/VectorBT calculation, selected replay, concurrent edit/submission races,
ownership, populated migration, retention and backup/restore. A Windows
maintenance failure with long destination paths was reproduced and corrected;
deep backup/restore, failure cleanup and containment have regression coverage.
The 100-cycle handle check remained within its bound. Ruff and whitespace checks
passed. A final native-harness restart preserved authentication, drafts, versions,
study/replay links and the original export bytes.

## Published preview baseline

The native **Backtest & Optimize** workflow is implemented: CSV inputs, automatic
native price preparation, shared-account VectorBT backtests, Optuna searches,
later-period checks and exact saved replay. An optional NautilusTrader Linux/WSL
runtime uses the same workflow with its declared execution rules.

This is an installable preview of the declared scope. Broader broker, Docker and
cross-version upgrade acceptance remains open. [Release notes](releases/0.1.0-preview.4.md),
[installation](DISTRIBUTION.md) and the [system map](SYSTEM_MAP.md) describe the
public distribution.

## What is delivered

| Area | Implemented scope |
| --- | --- |
| Portfolio | One to eight long NSE cash-equity CSV signal strategies, separate rules and allocations, one shared engine account. |
| Inputs | Upload CSV or reuse account-scoped saved signals; selecting a saved source starts no download. Daily and timed CSV examples are available in a collapsed help section. |
| Data | Automatic daily/one-minute selection, finest required interval across the account, full search-window planning, Historify reuse and missing-only native broker acquisition. |
| VectorBT | Real 0.28.5 portfolio calculations, daily/intraday/multiday execution, native fills, cash and fees, with reconciled strategy contributions. |
| NautilusTrader | Real 1.231.0 Linux BacktestEngine, independent strategy/position identities, shared INR cash and daily/minute inputs under a recorded OHLC model. |
| Optimization | Real Optuna 5.0.0 TPE or grid over selected settings and allocations, bounded trials, objectives and version-bound recovery. |
| Coverage | A fixed eligible signal cohort before trials; explicit price/instrument exclusions and no invented prices. |
| Later period | Earlier-only selection and a separate later simulation with fresh capital; positions cannot cross the boundary. |
| Results | Combined account, strategy contributions, Trades/Trials/Settings, saved runs and exact evidence export. |
| Replay | Recorded settings and frozen inputs; no new download or optimization. |
| MCP | Twelve account-scoped research tools through existing OpenAlgo authentication, explicit remote research scopes and bounded responses. |
| Handoff | Selected strategy-definition export with execution disabled. |

VectorBT and Optuna TPE are defaults. Nautilus is available in advanced settings
when its runtime is installed. Unsupported settings are not silently changed.
Older scanner reports retain their original evidence and execution policies.

Preview.4 also corrects recognized native NSE calendar seed errors with an
idempotent migration that preserves custom entries. Calendar planning follows
actual entry and holding requirements, including special sessions and optimizer
ranges. Worker shutdown preserves resumable progress. The ordinary Fyers history
adapter accepts its explicit successful no-data response and propagates exhausted
or malformed requests instead of returning a partial success.

## Verification at this preview

| Check | Result and scope |
| --- | --- |
| Final Windows research suite | **774 passed, 42 skipped**, with one existing Pydantic warning. Platform/optional checks remain separate from this run. |
| Additional targeted checks | Native calendar: **30 passed**. Fyers/history/acquisition targets: **105 passed**. These overlap the full suite and are not additive totals. |
| Native interface | **29 focused journey checks**, TypeScript, scoped Biome and production build; desktop/mobile keyboard interaction and CSV example downloads exercised. |
| Fresh Windows preview.4 ZIP | A fresh extracted directory and new locked environment passed setup/login, built assets, two-strategy real VectorBT calculation, real Optuna trials, exact selected replay, restart and export preservation. **40 artifact checks passed**. Graceful shutdown left no owned processes, listening test ports or worker lease. |
| Windows installation boundary | The fresh-directory check used an existing Windows host and cached packages with controlled Historify candles. It was not a clean OS image or a real broker login. |
| Linux installation | Earlier preview installation with new locked core/Nautilus environments passed setup, real HTTP/worker calculations, exact replay and an idempotent populated reinstall. Account, configuration and exported reports survived reinstall; owned processes and ports were cleaned up. |
| Nautilus boundary | **29 real native Linux checks** and **10 instrument-metadata checks**, plus cancellation, malformed response and repeated-run cleanup coverage. Real calculations through the Windows/WSL bridge completed. |
| Real broker path | Fyers daily preparation and acquisition of an absent minute window were exercised in an existing OpenAlgo installation. Frozen OHLC matched its native Historify archive; replay and subsequent archive reuse were verified. |

The daily archive-reuse check still made requests for uncovered windows that
returned no usable new candles. Zero newly stored candles does not imply zero
broker requests. Exact saved replay performs no new acquisition.

The final Windows installation check preceded publication preparation and frontend
dependency patches. Its calculation code and Python dependency locks remain the
tested preview.4 baseline; the frontend is rebuilt and checked after its updates.
Release assets contain a file-hash manifest and checksums identifying their final
contents. Historical checks are recorded in [VERIFICATION.md](VERIFICATION.md).
Private inputs, accounts, databases and machine-specific receipts are excluded
from Git and release inventories.

## Data and execution rules

Historify remains the mutable native price archive. Missing required coverage goes
through the connected broker's OpenAlgo history service, is saved and read back
there, then is frozen for calculation. Date-only signals with daily execution
rules remain daily. Timestamped signals or explicit intraday rules select
one-minute data. Stops, targets and trailing rules alone do not force minute data.

Price and instrument eligibility consider each strategy's maximum allowed holding
window before optimization. A successful absent-price response or unavailable
master symbol can record an exclusion. Authentication, rate-limit, server and
unknown request failures remain resumable acquisition errors.

VectorBT entry-bar protection and Nautilus's modelled OHLC matching are distinct
policies. OHLC-derived events are not observed ticks. Nautilus checks raw prices
against supplied native instrument metadata without rounding or guessing historic
tick sizes. Shared-account results and strategy attribution reconcile to the
selected engine's results. See [CONNECTORS.md](CONNECTORS.md).

## Remaining boundaries

- Calendar admission covers 2025–2026 and rejects required special sessions with
  unconfirmed hours. Security-specific auction eligibility, closing-auction
  behavior and exchange matching chronology are outside this model.
- Real broker acceptance covers the recorded Fyers daily/minute cases. Other
  adapters have controlled checks, not universal live-account certification.
- Public CI built the Docker image and probed both locked Python environments.
  Full container setup, application startup, restart and restore acceptance remain
  separate from these build and dependency checks.
- The targeted NSE calendar migration and populated same-version reinstall are
  verified. Broad cross-version update, restore and rollback coverage, clean OS
  installation and unaided consumer acceptance remain open.

The connector does not import arbitrary engine programs or implement shorts,
derivatives, leverage, shared margin, corporate-action cashflows or observed-tick
replay. Nautilus additionally excludes trailing stops and configured nonzero
slippage. Optuna Dashboard and a general rolling walk-forward builder are outside
this preview. Research export does not activate paper/live strategies.
