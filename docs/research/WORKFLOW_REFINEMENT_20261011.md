# Research workflow refinement plan

Date: 11 October 2026. Status: implementation, local activation and composed
Windows/Linux runtime/package qualification complete; public publication separate.

## Implementation checkpoint

The recovery, result eligibility, independent diagnostics, CSV-first entry and
save/reuse packages are implemented. Calendar failures now expose compatible
actions and bounded session details. Daily acquisition preserves recorded hours;
legacy missing hours can be recovered only from their verified original evidence.
Automatic selections prepare one independently verified report using frozen
inputs, then support **Save tested setup** and reuse without another search or
price download. An unchanged original remains a research reference, not a trading
recommendation. Intraday chosen setups preserve original execution timestamps
while using session dates for the setup's period fields.

Financial results commit before optional Optuna diagnostics, which run in a
bounded, cancellable subprocess with their own status. Numerical jobs take queue
priority over optional analysis. New compatible automatic runs retain original
and selected account curves only when their recorded comparison basis and actual
timestamps align; legacy results are not rewritten to manufacture this chart.

Entry previews CSV counts and dates before creating a draft. Experiment navigation
is now Overview, Setup, Results and Saved setups, with professional tools kept
inside Results. A chosen setup appears before collapsed setup history. Chart
replay fits its visible candles, and progress distinguishes scanner and market
context symbols from recorded counts.

Final keyboard acceptance corrects dialog focus return for saving and importing,
and legacy result aliases preserve the viewed optimisation/backtest list rather
than following the newest job. Research-layout reduced-motion support applies to
library dialogs as well as reports. Final frontend/native-layout qualification
passes 681 cases; targeted browser checks also cover invalid/timed/mixed import
previews, narrow chosen/report screens and Back/reload of study/trial context.
Actual 200% zoom and reduced-motion browser emulation remain unverified because
the available browser controls did not expose them.

Controlled Chrome acceptance has exercised preparation of an automatic baseline,
saving its named tested setup, reopening the chosen setup and reuse into Setup
with its original minute holding rule. This uses isolated evidence, not a fresh
broker download. Targeted qualification is recorded in [STATUS](STATUS.md);
broader journey and accessibility coverage remain explicitly unverified below.
The complete core inventory, corrected test-only follow-ups and actual final-UI
package/fresh-install gates now pass on Windows/Linux, with unchanged runtime
and inventory guards retaining the original failed-run evidence. Normal-app
activation preserves all 60 runs and configuration and passes
172 installed compatibility checks. Public preview history has been joined into
the reconciled development branch. Public preview.7 remains unchanged until the
separate preview.8 publication step is performed.

This plan closes the fourteen workflow steps inspected in the current desktop
audit. The main journey is usable but requires users to learn research terminology,
interpret technical failures and assemble the result-to-reuse path themselves.
Refine the existing native application rather than add another research interface.
This sequence precedes broader asset and authoring expansion in DELIVERY_PLAN.

## Intended trader journey

Research library → import scanner history or CSV → review inferred signals and
essential assumptions → Optimize automatically → visible price/search/check
progress → inspect the conclusion and report → save a tested setup → reuse it on
another capture. Backtest remains a separate, easily available action.

Returning users can reopen a result, continue a compatible paused run, inspect
trades, compare candidates or reuse their chosen setup without repeating import,
acquisition or calculations merely to view saved evidence. Unsupported requests
stop at admission with a concrete explanation and a useful alternative.

Automatic optimisation searches the supported bounded space and challenges its
findings. It must never promise a universal optimum or confuse the highest search
score with a validated trading recommendation. Retaining the original as a
reference does not establish that the original strategy is suitable for trading.

## Phase A: establish recovery and evidence contracts

This is the first implementation package. UI actions must be derived from the
backend's actual evidence and permitted transitions, rather than inferred from
an exception string or a generic completed flag.

- Reproduce the observed stock-market execution-hours mismatch with isolated
  evidence. Trace calendar admission, saved context, preparation/recovery and
  original versus current session-hours representations. Distinguish a genuine
  evidence conflict from a serialization or assembly defect before choosing a fix.
- Return a structured failure code, failed stage, first affected session, actual
  and expected hours, retryability and allowed recovery actions. Keep bounded
  details behind a disclosure; the primary message names the problem and next step.
- Offer Resume only for compatible recoverable checkpoints. An incompatible
  checkpoint offers a separately identified new run or corrective setup action;
  it never overwrites the old result, silently changes hours or discards evidence.
  Authorization required for a broker refresh is determined by native account state.
- Bind every result view and save action to experiment, source job/artifact,
  candidate settings identity, engine/recipe, actual data interval, frozen calendar,
  data cohort, evaluation period and validation evidence. Extend existing contracts
  where possible, preserving old report and recipe identities.
- Keep candidate outcome, validation status, job status, diagnostics status and
  the user's decision as separate facts. Planned outcome vocabulary: improvement
  supported by recorded checks; no reliable improvement; insufficient evidence;
  incomplete or blocked. A completed custom search remains explicitly unvalidated
  unless the relevant checks actually exist.
- Resolve save/use/replay eligibility from the same evidence contract. Existing
  exact-result ownership, event, condition and artifact fences remain mandatory.
  If an automatic report lacks compatible candidate evidence, prepare it once in
  a bounded step. Do not remove the exact-backtest requirement solely through copy.

Acceptance: a real mismatch is diagnosed with session-specific details and cannot
enter a pointless resume loop; a valid pause survives restart without redoing
completed acquisition; historical reports and exact replay retain their values;
duplicate clicks and stale revisions cannot save a different candidate.

## Phase B: publish findings promptly and explain progress

This implements the already planned independent diagnostics dependency before
the new completion screen depends on it.

- Publish committed numerical findings when calculation and required validation
  finish. Optional Optuna charts/importance analysis have independent bounded
  preparation, cancellation and retry. Their failure cannot erase a valid report
  or keep the primary job appearing unfinished. Required checks still block a
  validated outcome when incomplete.
- Use the existing Historify preparation path: infer daily/minute requirements,
  inspect matching native coverage in batches, acquire only absent windows through
  OpenAlgo, and freeze the shared union once. Profile coverage versus feature
  preparation versus calculation separately; optimise a measured bottleneck.
- Reuse unchanged native data and checkpoints across setting changes when their
  requirements are compatible. Widening a holding range or timing union adds only
  new required coverage. Price inspection never initiates acquisition.
- Present Read signals → Prepare prices → Search settings → Check → Results.
  Show the current stage, elapsed time and actual completed/total work. Price work
  distinguishes scanner symbols, market-context symbols, available candles,
  acquired candles and missing/unavailable data. No unexplained 143/144 mismatch.
- Pause, Resume, Cancel and return-later controls reflect actual worker support.
  Show the saved checkpoint and what will continue. Distinguish cancelled,
  interrupted, failed and legitimately unavailable broker data.
- Use short stage transitions and a restrained active-stage animation. Honour
  reduced motion, provide text status and avoid fake percentages or unsupported ETA.

Acceptance: repeated compatible runs make no unnecessary history request; missing
data and failures remain explicit; report availability does not wait for optional
diagnostics; cancellation/fencing remain bounded; performance is compared on
identical frozen daily/minute inputs without changing financial results.

## Phase C: make results, saving and reuse one connected journey

- Put a plain outcome first, followed by selected settings, original comparison,
  final-period evidence, exclusions and one primary next action. Search counters
  and highest exploratory score move into study details and carry their period.
- Separate selection, challenge and final-check periods throughout report,
  comparison, trial detail and export. Do not visually compare unlike periods,
  cohorts, currencies or costs as if they were equivalent.
- Open the authoritative report directly after completion. Reuse compatible
  recorded ledgers/curves; explain any necessary one-time exact materialisation
  instead of asking users to guess why another backtest is needed.
- Provide Save tested setup when eligible, with an inferred editable name and
  optional note. Compose the existing Keep/Choose semantics safely and idempotently;
  do not turn saving into live activation. Exploratory or rejected candidates can
  be saved for research with an explicit evidence status, never promoted by default.
- Keep decision recording, comparison shortlist and executable setup selection
  distinct underneath a clearer interface. Save draft remains a setup action;
  shortlist belongs to comparison; Keep/Reject/Revisit remains research judgement.
  Explain disabled actions beside their control.
- After saving, offer Use with new signals, inspect saved report and exact replay.
  Reuse previews retained rules, allocations, capital/cost assumptions, scope and
  date requirements. Confirm data mapping where necessary, never drop a condition.
- Derive useful version titles from source study/trial and changed rules. Keep
  automatic draft backups accessible in history rather than mixing them with
  deliberately chosen setups at equal prominence. Retain old names and IDs.

Acceptance: completed study → report → named saved setup → new CSV preview can
be followed without manual trial-range configuration or an unexplained duplicate
run; selected clocks and conditions survive; back navigation restores period,
trial, filters and scroll; saved choices remain inspectable after reload/restart.

## Phase D: simplify entry and navigation

- Start new research from CSV, Chartink or saved signals. Infer a name from scanner
  metadata or filename and let users edit it after preview. Upload validates
  counts, unique symbols, dates/timestamps, duplicates and supported asset/venue.
- Ask only essential unresolved trading assumptions such as starting cash, sizing
  or costs. Auto-infer interval and supported methods; keep engine and custom-search
  controls advanced. Never silently revise saved scopes or financial settings.
- Make the first-use Chartink guide a genuine Connect Chartink guide. Returning
  users see a concise next action and connection status when it can be verified;
  do not invent a connection status from a local flag. Manual CSV remains available.
- Preserve the experiment container and existing deep links. Simplify the main
  navigation toward Overview, Setup, Results and Saved setups; studies/backtests
  become clear result filters, and decisions/shortlist/comparisons are contextual
  review tools. Sources, archive and legacy runs remain reachable as secondary tools.
  Migrate labels/routes with compatibility aliases, not an all-at-once data rewrite.
- Use meaningful result titles, operation, date, key rule differences and status.
  Remove repeated scanner headings where the experiment already supplies context.
  Overview highlights the exact active/stopped run, latest conclusion and chosen setup.
- Keep Optimize automatically visible near essential setup information, using a
  responsive action area. Advanced details and multiple tab rows do not precede
  the basic decision to run. Warn about repainting history near admission in one
  factual line; it is a limitation of supplied signals, not a reconstructed scanner.
- Empty review/library states link directly to the action that creates content.
  Use action-specific busy labels: saving a version must not say Starting a run.

Acceptance: a first-use trader can identify how to import and optimise without
knowing the term study; returning users bypass installation prose; a saved older
scope remains unchanged; direct URLs and browser Back retain context.

## Phase E: retain professional inspection without crowding the main path

- Keep configurable trial columns, sorting, pagination and the settings/statistics
  dialog. Display the candidate's evidence status and changed rules, preserve column
  preferences and make per-row actions accessible. Add aligned original/selected
  candidate curves only after the evidence contract can retain their actual basis.
- Retain the continuous tear sheet with headline metrics, meaningful section
  navigation and optional detail. Remove duplicate Settings affordances. Surface
  skipped/excluded counts and reasons near the headline, distinguishing rule/capital
  skips from missing data. A unavailable metric has a short reason, never fake zero.
- Benchmarks and market-condition diagnostics use available native evidence and
  carry independent status. Do not auto-download unsupported macro/volatility data
  or present estimated signals as facts. Native Optuna parameter/importance/contour
  tools remain available for deeper inspection without becoming compulsory steps.
- Fit the trade chart to its recorded candle range and place entry/exit in useful
  context. Keep native indicators, drawing tools, saved-price feed and replay.
  Distinguish daily candle display coordinates from recorded execution dates; do
  not change underlying timestamps to remove a confusing label.
- Preserve exact selection and replay state on return to trades/report. Verify
  recorded intraday clocks, same-day exits, gaps and final/open trades as well as
  the audited daily example. Replay never exposes future bars, markers or values.

Acceptance: a trader can move from a headline concern to its trial, trade, chart
and back without losing the question; chart fit works for short/long saved ranges;
display changes cannot alter recorded trades, dates or calculation semantics.

## Phase F: qualify the full journey and deliver one consistent installation

- Repeat human walkthroughs for first-use CSV, installed-extension import,
  date-only, timed and mixed inputs, multiple strategies, unsupported inputs,
  fully cached/missing native data, original retained, improvement selected,
  insufficient evidence, interruption/restart, diagnostics failure, saved choice,
  changed CSV reuse, comparisons and legacy results. Use isolated development
  data; separately record any authorised real-broker qualification.
- Test keyboard focus, Escape/return focus, names, charts' text alternatives,
  non-colour statuses, live progress announcements, measured contrast, reduced
  motion, 200% zoom and narrow screens. Avoid claiming accessibility from pictures.
- Run targeted contract/backend/frontend regressions and build checks per change.
  Resource-owning changes receive native fd-audit. Run installer/platform checks
  when touched and the complete supported Windows/Linux release matrix once for
  the actual release candidate; a documentation edit does not justify a repeated
  full Windows environment rebuild.
- Activate only qualified source and matching frontend. Preserve accounts,
  Historify, existing settings, studies and artifacts; record identities and rollback.
  Update guides/README/extension instructions to the implemented journey.
- Reconcile draft integration with public main and qualify preview.8 before
  packaging. Keep source push, local installation and public packaged release
  separate states. Update Graphify with implementation, verification and pending
  dependencies; plan entries are not completion evidence.

Acceptance: the same tested package supports clean install and populated upgrade,
all advertised workflows have scoped evidence, public instructions match the
published package, and no consumer must assemble a separate VectorBT/Optuna UI.

## Audit coverage and implementation ownership

| Audit steps | Work package | Existing seams to extend |
|---|---|---|
| 6: failure/recovery | A, B | research_stock_market_context, research_portfolio, worker/checkpoints, ResearchRunProgress |
| 7, 11: custom/automatic findings | A, B, C | PortfolioStudyWorkspace, AutomaticResearchFindings, decision targets, report contracts |
| 10, 12, 14: decisions/save/reuse/review | A, C, D | research_decisions, research_chosen_setups, research_setup_reuse, ResearchResultReview, ResearchChosenSetup, ReuseChosenSetup |
| 1–5: library/import/naming/overview/setup | D | ResearchLibrary, ScannerResearch, research_library, PortfolioBuilder, existing Chartink connector |
| 8–9: trial comparison/tear sheet | C, E | PortfolioStudyWorkspace, PortfolioContinuousReport, ReportCustomize, report preferences |
| 13: charts/replay | E | SavedTradeChart, SavedTradeChartDialog and native chart primitives |
| Evidence gaps across all steps | F | Existing research tests, compatibility tooling, installation and release workflows |

Implementation order: A → B → C → D → E → F. Small naming, accessibility and
chart-framing improvements may accompany their owning package, but no parallel
redesign should invent different result/save semantics. Phase F's targeted checks
apply throughout; its complete publication gate belongs to the final candidate.
The existing bounded v7 search, older saved recipes and native host/engine pins
remain unchanged unless a separately qualified numerical change is necessary.
