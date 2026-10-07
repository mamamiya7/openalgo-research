# Professional trader journey audit

12 September 2026 · Audited application revision `52ee878` · **Current experience, followed by a proposed plan.**

[Open the visual walkthrough](pro-trader-audit.html) · [Implementation sequence](DELIVERY_PLAN.md#10-trader-audit-priorities--12-september-2026) · [Original 34-journey target](PRODUCT_JOURNEY_MAP.md)

## Verdict

**The research calculations and evidence are substantially stronger than the journey connecting them.** The app is useful for an informed tester within its declared cash-equity scope. It still asks a new trader to understand too many record types and reconstruct several important next steps. I would continue releasing it as a preview, with its limits stated, until the complete decision and return journeys are accepted.

Keep the native OpenAlgo integration, automatic data requirements, shared-cash calculation, real Optuna exploration, compact trial inspector, continuous tear sheet, and exact saved evidence. The next investment should close the path from a result to a deliberate decision and reusable setup. Another page of metrics, another engine selector, or a broad asset menu would add little until that path works.

This does **not** mean every user must optimize and compare. There are two legitimate paths:

- **Quick test:** scanner/CSV → review trading rules → backtest → inspect → keep/reject or export → reopen later.
- **Research:** baseline → optimize → investigate actual candidates → optionally compare → test the chosen candidate on reserved dates → decide → reuse or review later.

Both paths must preserve the exact configuration, prices, periods and assumptions behind the result. A human decision and the optimizer's best score remain different things.

## What was actually checked

The normal installation responded successfully, its calculation worker was online, and all 11 runtime files from the latest installation matched the expected hashes. It retained 50 completed and seven historical failed jobs, with no active jobs. Git `origin/main` matched `52ee878`. An unchanged installation did not need another restart.

Chrome's normal-account tabs were at login. Fresh interactive UI work therefore used **controlled native Research routes and the current React build**, with synthetic three-symbol candles and actual VectorBT/Optuna calculations. This was an audit environment, not a replacement installation for the user. The host shell's authentication and broker label were fixtures. Its “Live Mode” label did not represent an active brokerage connection, and no trading service or orders were exercised.

The walkthrough created a draft through the UI, reused saved signals, ran a real 25-proposal study, selected a real contour point and trial, shortlisted it, opened its report, investigated a drawdown, ran the winner's reserved period, compared both compatible and incompatible results, saved a decision with existing later evidence, and reopened its history. It also inspected setup reuse, column selection, dark mode, and a 390px mobile layout. **26 fresh screenshots were saved and inspected.** No old screenshot was counted as current evidence.

Graphify first refreshed all 23 pending sources, preserved unchanged-source IDs and checked 19 retrieval paths. Its graph and narrowed queries located the relevant implementation and contracts. Source inspection then covered every J01–J34 journey. Graph relationships were navigation aids, not proof of UI or broker behavior.

**Limits:** no fresh authenticated broker download, cold/warm speed test, Chrome-extension capture, interrupted-worker recovery, multi-strategy calculation, Nautilus execution, cross-device conflict test, or install/restore cycle was performed in this audit. Those have source or historical evidence only. The four preserved installation-specific frontend customizations were not visually revalidated behind the login wall. This is an expert walkthrough, not an unaided new-user study or full accessibility certification. Wider asset classes remain outside the current admitted product scope.

## What makes sense

| Strength | Why it matters | Fresh evidence |
| --- | --- | --- |
| Small idea-entry dialog and server-saved drafts | A trader can record a question without committing to a calculation | 02–04 |
| Saved signals, relevant rule drawer, suggested optimization ranges | Avoids rebuilding inputs and solves the former empty-search trap | 04–06 |
| Progress with CSV dates and real trial counts | Makes the wait understandable without noisy notifications | 07; broker acquisition itself not re-tested |
| Optuna history, importance, slices and contour linked to real proposals | Exploration produces inspectable candidates; an estimated contour is not invented evidence | 08–10 |
| Trial details in a compact dialog | Settings remain next to the investigation, without scrolling through 100 trials | 10, 22–23 |
| Continuous account tear sheet and drawdown investigation | The result has a meaningful hierarchy and leads into actual saved trades | 11–13 |
| Compatibility gates and frozen comparisons | Different test inputs do not silently produce misleading deltas or one combined curve | 17–18 |
| Append-only decisions with exact report links | The reason for a choice survives later analysis or a changed opinion | 19–20 |

## Findings that should change the plan

### F1 — Result identity is hard to see; return navigation loses the task

**Observed:** three different backtests in the same experiment had the same name, date, and “Results ready” label. They represented a full-period baseline, a candidate report, and later-period evidence. The overview's primary action was still **Continue setup**. Saved versions appeared as Setup 1/2/3 with a date and strategy count, without a rule difference or result role. A report opened from a scrolled parameter chart inherited that scroll position, leaving its identity and actions above the viewport. Both **Back to studies** and **Back to study** could be present. See 11, 15 and 21.

**Impact:** a returning trader can inspect the wrong period, rerun unnecessarily, or lose the candidate they were evaluating. Clean spacing alone does not solve this.

**Change:** establish one result descriptor and one navigation contract. Show role, source/trial, exact period, meaningful version name, and current review state. Forward navigation to a different report starts at its identity; returning restores the originating trial, filters, axes, page and scroll. Overview offers the last meaningful next action. Saved versions show a brief rule difference and their linked result before reuse.

### F2 — Validation works from the original winner, but the chosen-candidate journey is incomplete

**Observed:** the original study winner exposed **Test later period**, which produced an actual later-period report. Compatible comparison exposed report and decision actions but no way to launch missing validation for a chosen member. **Source verified:** a prepared nonwinner's child report has the reservation removed; generic replay of that child is not a correct shortcut to the original reserved period. See 14 and 18–20.

The reserved-period switch shows “first 80% of signal dates” before launch, without actual boundary dates or the effective eligible signal cohort. A baseline run over the full range can already use dates that a subsequent study labels reserved. The application must preserve the distinction between planned reservation, recorded calculation, recorded opening, decision use, and unknown prior history.

**Change:** one canonical candidate action resolves the original study, exact configuration, frozen input and reservation. It opens a verified existing later result or explicitly prepares the missing result. Before that calculation, show actual selection/later dates, existing recorded use, and the fresh-cash/no-carried-position basis in a compact review. Carry this context through the report and decision. Do not infer “untouched” from an empty history table.

### F3 — Keeping one result requires an unnecessary comparison; Keep does not finish the product journey

**Observed/source verified:** a decision is made from a comparison member, and comparison requires two to four ready reports. A trader with one baseline cannot directly keep or reject it. The decision dialog itself is concise and saves a reason plus exact existing later evidence. The resulting history has no connected **Use as chosen setup** or subsequent review action. After saving, comparison still presents a generic **Decision** action rather than a visible current choice. See 16, 18–21.

**Change:** allow direct decisions on an exact baseline or candidate while retaining existing comparison-backed history. Keep means retain an idea; a separate deliberate selection identifies the experiment's chosen reusable setup. Show current choice where the candidate is inspected. Revisit retains a reason and can later gain an optional review date. It must not automatically schedule notifications or trading.

### F4 — Fairness is protected, but the user has no repair path

**Observed:** comparing the full-period baseline with a selection-period candidate correctly disabled deltas and a combined curve. The explanation listed Signals, Eligible signals, Price timestamps, Historical prices, Trading calendar and Evaluation period. It did not lead with the actual date difference or offer a matched baseline calculation. See 17.

**Change:** show the primary material difference first, using actual values. Offer **Create matching baseline** only when a precise equivalent cohort/period/account recipe can be established. It creates a new result and preserves the original. Do not trim a curve and label it a newly funded backtest. Incompatible reports may remain inspectable and decisions may remain notes; they do not become a valid ranking.

### F5 — Essential trading assumptions are hidden while less useful names occupy the form

**Observed:** the initial setup asks for a research name and then a separate Portfolio name defaulting to “My portfolio.” The collapsed strategy row shows the filename/count but not its target, stop, holding rule or entry convention. The drawer contains useful TP, SL, holding, trailing protection and costs. Data source says “Prices from OpenAlgo,” but resolved market/frequency and actual reserved dates are absent from the first review. See 03–06.

**Change:** derive the portfolio/run name from the idea unless deliberately renamed. Show a single compact rule summary per strategy and a compact resolved data/period line. Reuse the real data planner: symbol resolution, daily/minute frequency, whole search requirements and native-cache coverage remain automatic. Ask only for unresolved consequential meaning. Make the modeled per-trade size, fee/slippage and same-bar fill basis discoverable without turning the form into documentation.

**Scope clarity:** the current CSV workflow tests execution and exits on supplied scanner observations. It does not regenerate the scanner's indicator rules over historical universes. “Add strategy” should say **Add signal CSV** in this mode; a later rule connector is a distinct entry mode.

### F6 — More statistics exposed duplicate choices

**Observed:** searching Columns for “Starting capital” produced two identically named Starting capital choices. Final equity, Net P&L and Realized equity also have original-summary/catalog overlap in the available entries. Search additionally matches descriptions, which can return other labels. See 23.

**Source verified:** catalog metrics are appended when their keys differ; equivalent concepts are not reconciled. This is a presentation/registry issue, not proof the calculations are wrong.

**Change:** define canonical user-facing metric concepts with unit, account basis, period and provenance. Merge known equivalent aliases while keeping genuinely different definitions explicit. Preserve all original saved values and engine-specific variants in detail/export. Apply that same catalog to the report, trials and comparison. Never deduplicate by label alone or silently substitute one Sharpe/drawdown definition for another.

### F7 — Loss investigation stops before the exact execution explanation

**Observed:** a drawdown opens marked-account context and overlapping trades. Clicking a symbol then opens *all* trades for that symbol, losing the selected drawdown window and exact trade. The row's outcome expands to a brief reason such as “stop; intraday exit.” The source has a separate Prices and fills section. See 12–13.

**Change:** connect those existing surfaces through an exact trade identifier. Open its candle/fill/rule/cost context, keep the investigation window, and return to the same row and chart. Mark daily OHLC execution as the declared model; it is not an observed minute/tick path. Add fresh native benchmark evidence separately so a trader can ask whether the result adds value relative to an appropriate reference.

### F8 — Long-running research lacks a complete operating lifecycle

**Observed:** active calculation has Cancel run and Activity. **Source verified:** durable progress, compatible interruption recovery and real proposal outcomes exist, but genuine pause, inspectable stopped segments and adding trials to a completed study are incomplete. Reopening old work is not a data refresh. Auth/calendar/quota repair remains partly external. See 07 and the journey matrix.

**Change:** first define scientific identity and execution segments, then expose Pause/Resume, Stop and Add trials where supported. Include a precise saved-work summary and repair destination for blocking data issues. Keep **Replay exact**, **Test newer data**, **Refine search** and **Add trials** separate actions with the correct retained lineage. No extra background notifications by default.

### F9 — Mobile and accessibility need task-level acceptance

**Observed:** the 390px layout reflows, dialogs focus the relevant control, and Escape closes the column dialog. However, eight experiment tabs require horizontal discovery, the study headline occupies nearly a screen before trials, and the trial table initially exposes score/return while drawdown and actions are offscreen. Nested page/table scrolling adds effort. Dark comparison text/table appeared legible in the checked state. See 24–26.

**Change:** reduce primary navigation, condense study status on narrow screens, and provide a mobile candidate row with return, risk and a visible details action. Preserve the detailed desktop table. Verify keyboard selection, focus return, semantic active navigation, chart alternatives, contrast, 200% zoom and reduced motion before release. Screenshots do not establish WCAG compliance.

## All 34 journeys, reconciled with the current app

**Observed** means a fresh controlled UI action or screenshot in this audit. **Code** means current source inspection only. **Planned** means the complete native journey is absent; adjacent functions do not count as completion. “Partial” can include strong implemented pieces and a missing return/save/next step.

| Journey | Current health and evidence | What still needs to happen |
| --- | --- | --- |
| J01 Capture an idea | Working core · observed 01–03, code library | Clear naming and stage-aware overview; notes/tags available in details |
| J02 Test existing signals | Working core · observed saved-source and calculation paths 04–07; fresh upload/extension not repeated | Compact resolved input/rules review; explicit signal-CSV scope |
| J03 Build signals from rules | Planned · code | Causal template/indicator connector, feature warmup and source versioning |
| J04 Import outside research | Planned · code | Distinguish reproducible setup/report import from an attachment or signal file |
| J05 Resolve ambiguous input | Partial · code parser | In-place symbol/time/column mapping with preview and saved interpretation; no broad silent guesses |
| J06 Prepare missing data | Strong core, partial repair journey · code; 07 progress only | Actual requirement summary, contextual repair and authorized cold/warm broker receipts |
| J07 Read a baseline | Partial · observed report surfaces 11–14, code | Matched baseline identity, native benchmark and direct retained decision |
| J08 Diagnose a loss | Partial · observed 12–13 | Preserve window and exact trade into candle/fill/rule/cost detail and back |
| J09 Adjust a known setup | Working core · source verified; actions observed 10–14 | Show parent/diff and offer a financially valid paired comparison |
| J10 Optimize settings | Working search, partial research setup · observed 06–10, 22 | Actual reservation/cohort review, baseline relation and separately versioned eligibility constraints |
| J11 Optimize allocations | Partial · code only | Compose saved components, show shared-cash/risk meaning, supported concentration constraints |
| J12 Monitor a search | Partial · observed live trial progress 07; activity source verified | Pause/stop segments and useful partial-result continuation |
| J13 Explore parameters | Working core · observed real contour-to-trial 09–10 | Preserve full view state; make a selected region a deliberate child hypothesis |
| J14 Test a manual hypothesis | Partial · code; Adjust action visible | Explicit manual candidate provenance; adding a proposal to an existing study is not implemented |
| J15 Continue unchanged study | Planned complete continuation · code | Separate scientific identity/budget, retained sampler state and Add trials |
| J16 Refine a search | Partial · code; editable search visible | Named child study with changed ranges/reason, proper reuse rules and return |
| J17 Shortlist candidates | Working core · observed 10, 16 | Visible current decision, optional global retrieval when needed, chosen-setup distinction |
| J18 Compare fairly | Partial with sound gates · observed 17–18 | Matched-baseline repair, actual assumption differences and canonical validation action |
| J19 Evaluate reserved data | Partial · original winner exercised 14; missing candidate path code verified | Exact nonwinner launch, exposure review and linked selection/later decision flow |
| J20 Walk forward | Planned · code | Frozen window/refit/carry-reset/aggregation contract before charts or scheduling |
| J21 Stress assumptions | Planned coherent scenario journey · code | Recorded cost/slippage/delay/neighborhood scenarios and degradation results |
| J22 Transfer market or period | Partial within cash scope · code | Explicit new-universe/date recipe, changes and admitted instrument profile |
| J23 Explain engine disagreement | Planned · code | Equal-input rerun and first material order/fill/account difference; no engine voting |
| J24 Combine strategies | Partial · code shared-cash core | Use chosen saved components; distinguish contribution from standalone counterfactuals |
| J25 Inspect exposure/capacity | Partial · exposure surfaces code verified | Concentration/correlated-loss investigation; capacity only with relevant liquidity evidence |
| J26 Recover interrupted work | Partial with durable safeguards · code, not interrupted this audit | Precise saved-work/repair/resume flow; real pause is a separate capability |
| J27 Return days later | Partial · observed 15, 20–21 | Find the actual last task and chosen result; restore context beyond a browser session |
| J28 Keep/reject/revisit | Partial · observed saved decision 19–20 | Direct single-result decision, chosen reusable setup and clear next review |
| J29 Reproduce versus refresh | Partial · code; report actions observed | Explicit newer-data run with retained source/settings diff; old inspection stays download-free |
| J30 Export/share | Partial · export action observed; source evidence bundle code verified | Self-contained readable report with optional study/comparison/decision appendix |
| J31 Review paper/live observations | Planned · code | Separate opt-in observation connector linked to an exact chosen setup; no automatic execution |
| J32 Organize/retire | Partial · code library/archive/retention | Better retrieval/status and complete chosen-research retirement; archive is not cancel or delete |
| J33 Upgrade/change broker | Partial · fresh installed-file/health check, rest code/history | Exact release upgrade/restore and per-broker/profile/interval admission; old evidence stays readable |
| J34 Multiple tabs/devices | Partial · code revisions/ownership; no fresh concurrency test | Full view restoration and conflict acceptance across authenticated sessions |

The original journey map is a dated design target. Its older “Proposed” labels do not override the implemented study, shortlist, comparison or decision features recorded here. There is no useful single percentage for completion across these unlike journeys.

## Proposed page responsibilities

Keep existing records and native components. Change how the user reaches them, without a storage rewrite merely to remove tabs.

| Surface | First thing the trader sees | Primary action / deeper controls |
| --- | --- | --- |
| Library | Idea, market, last research state, chosen result when present | New idea or reopen the actual last task; studies and sources remain searchable views |
| Experiment overview | Research question, baseline/candidate/validation/decision state, exact chosen setup | Continue research; recent results distinguish role/trial/dates; history folds below |
| Setup | Source + signal range, compact strategy rules, shared capital, resolved market/frequency, actual reserved dates | Run backtest or Start study; strategy drawer for TP/SL/trailing/costs; version history here |
| Backtests | Named roles and linked reports | Report → investigate trade → optional compare/validate → decision; exact replay/new-data actions separate |
| Studies | Objective, real progress and readable return/risk of the objective leader | Inspect candidates, refine or continue when supported; parameter charts stay inside this workspace |
| Review | Shortlisted candidates, comparisons, reserved evaluation, current decisions | Work on the selected candidate; comparison optional; choose reusable setup explicitly |

Proposed experiment navigation: **Overview · Setup · Backtests · Studies · Review**. Shortlist, comparison, validation and decision are contextual views within Review; saved versions live with Setup. Existing deep links continue to resolve. This is a proposed information structure, not a shipped UI change or a demand to add another empty tab immediately.

## Proposed responsibilities: automatic, trader choices and detail

This is the target division of responsibilities. It includes existing capabilities and proposed journey improvements; the matrix above records what is implemented today.

| Automatic | Deliberate trader choices | On-demand detail |
| --- | --- | --- |
| Scanner/source name, signal range/count, saved-source reuse, canonical symbol mapping when unambiguous | Idea/notes; sources/universe; capital, sizing, TP/SL/trailing and holding intent | Parser mapping and excluded signal reasons |
| Required daily/minute streams and whole candidate windows; Historify reuse, gaps, bounded broker requests | Actual research/reserved date plan, modeled fees/slippage/fills, objective/ranges/budget | Exact request windows, cache/download counters, engine/version receipts |
| Existing exact report/evaluation lookup, request idempotency, immutable parent links | Keep/reject/revisit, chosen setup, new data/child search/extra budget | Historical use, prior revisions, exact original evidence |

The app must not ask users to select a database or manually route data between engines. Consequential missing intent still needs a focused question; “automatic” does not mean silently inventing an instrument, fill policy, adjustment basis or historical fee.

## Asset-class assessment

This table is a **future admission plan**, not a new claim about broker or engine capabilities. Current support remains long NSE cash-equity signals, one to eight strategies, shared INR cash and supported daily/minute requirements. See [ASSET_PROFILES](ASSET_PROFILES.md) for the detailed contracts.

| Profile | User chooses when admitted | Automatically resolved prerequisites | Current status |
| --- | --- | --- | --- |
| NSE cash long | Signals, sizing, exits, holding, cost and reservation intent | Instruments/sessions, D/1m requirements, complete candidate coverage | Current bounded workflow; finish the full decision/review path |
| Other cash/ETFs | Security/venue/currency and price or total-return intent | Dated symbols/actions/distributions/FX and comparable benchmark | Future admission; ETF market price is distinct from NAV |
| Shorts/long-short | Intraday versus overnight, exposure and borrow model | Product/borrow/financing facts where available | Requires financing, collateral and forced-exit accounting |
| Futures/commodities | Actual contract, lots, roll/expiry/delivery intent | Dated contracts, multipliers, both roll legs, settlement/session data | Requires margin, MTM and lifecycle handling |
| Options/multi-leg | Underlying, exact/dynamic legs, selection/expiry/hedge and package rules | Historical contract universe, all potential legs, underlying and lifecycle facts | Requires historical selection, multi-leg execution and account model |
| FX/CFDs/currency derivatives | Exact product, pair, leverage and holding | Quote/conversion/financing/contract streams | Separate profiles and currency/margin semantics |
| Crypto spot/perpetual/futures | Venue/product, sizing, funding/leverage intent | Units, sessions, conversion, funding/settlement facts | Separate spot/derivative/inverse accounting where relevant |
| Bonds/funds/SIP | Cashflows, contributions and return question | Instrument cashflows or NAV/unit allocation evidence | Separately deferred; stock-candle assumptions do not apply |
| Cross-asset portfolio | Components, base currency and collateral policy | All admitted profiles and coherent valuation clocks | Requires a coherent shared account, not added independent equity curves |

Do not enable a profile until one example completes **select → resolve native data → calculate → diagnose → compare → save/replay**, including a meaningful unsupported-history case. Independent feasibility probes can run earlier.

## Prioritized execution plan

The authoritative dependency and acceptance sequence is [DELIVERY_PLAN section 10](DELIVERY_PLAN.md#10-trader-audit-priorities--12-september-2026). This audit changes the immediate priority from adding more analysis to completing the existing trader paths.

1. **A — Identity, periods, assumptions and navigation.** Define the shared descriptor and return context first; show actual resolved periods/rules; fix repeated names, wrong scroll entry, duplicate metric concepts and stage-aware continuation. Establish the matched-baseline recipe before a UI repair action.
2. **B — Exact validation and direct decisions.** Resolve original candidate identity/reservation; launch or reopen correct later evidence from any supported candidate; permit direct single-result decisions. Preserve recorded/unknown data use and historical comparison decisions.
3. **C — Chosen reusable setup and repeat use.** Promote the deliberate choice, show it in Overview/Library, preserve named versions, and add explicit newer-data/related-scanner review without copying settings or rewriting prior results.
4. **D — Reliable operation and diagnosis.** Contextual data recovery; scientific identity plus execution segments before pause/extension; exact trade-to-fill investigation; fresh native benchmark and coherent metric presentation. These are separate bounded increments with shared prerequisites.
5. **E — Public handoff and release acceptance.** Readable portable output, then the exact-artifact install/upgrade/restart/restore and unaided cash-equity journeys, including normal-app cold/warm data checks. Release engineering runs throughout A–E. Extension publishing remains a separate approved submission track.
6. **F — Professional depth and expansion.** Scenario stress/walk-forward, saved component portfolios, engine discrepancy diagnosis, causal rule/import connectors, then individually admitted assets and observation connectors. Keep these named and dependency-bound; they are not all prerequisites to an honest cash-equity preview.

## Acceptance that would change this audit's conclusion

- A first scanner user can explain the signal dates, execution rules, sizing basis and reserved dates before Run, finish a baseline, save a direct decision and reopen the chosen setup without coaching.
- A researcher can select a repeated **nonwinning** proposal, open its exact report, create a valid matched baseline, run that candidate's reserved period, decide, and reopen the same evidence later. No parameter copying or guessed child replay.
- A previously used/unknown evaluation period stays labeled honestly. Incompatible reports offer a precise repair or remain explicitly inspection-only.
- A controlled interrupted price/search job retains native candles and completed outcomes; repair and resume respect the same identity. Completed-study extension is verified separately from recovery.
- A losing trade opens its actual candle/fill/rule/cost context and returns to the originating drawdown/trial. Different accounting definitions stay explicit.
- A returning user can distinguish exact replay from newer-data review, resolve a concurrent draft edit, archive/restore and find their chosen setup. Inspection itself creates no download/calculation.
- A new user installs the actual release artifact and completes both a daily-data and a required-minute-data journey. A second person can read the exported result without installing the app. Broker claims match separate authorized native acquisition/readback/reuse evidence.

## Source anchors

Source review at the audited revision supplements the screenshots:

- [Library, versions and navigation](../../frontend/src/pages/ResearchLibrary.tsx), [job/report transitions](../../frontend/src/pages/PortfolioResearch.tsx), [native library service](../../services/research_library.py).
- [Portfolio builder](../../frontend/src/components/research/PortfolioBuilder.tsx), [strategy rules](../../frontend/src/components/research/PortfolioStrategySettings.tsx), [requirements/capability contract](../../research/portfolio.py), [CSV interpretation](../../research/signals.py).
- [Native preparation and replay](../../services/research_portfolio.py), [Historify boundary](../../services/research_historify.py), [calendar](../../services/research_native_calendar.py), [run progress](../../frontend/src/components/research/ResearchRunProgress.tsx).
- [Study workspace](../../frontend/src/components/research/PortfolioStudyWorkspace.tsx), [trials](../../frontend/src/components/research/PortfolioTrials.tsx), [metric catalog assembly](../../frontend/src/components/research/portfolioTrialMetrics.ts), [Optuna identity/recovery](../../research/connectors/optuna_portfolio.py).
- [Report](../../frontend/src/components/research/PortfolioResults.tsx), [continuous report](../../frontend/src/components/research/PortfolioContinuousReport.tsx), [investigation](../../frontend/src/components/research/ReportInvestigation.tsx).
- [Shortlist](../../services/research_shortlist.py), [comparison](../../services/research_comparisons.py), [evaluation identity](../../research/evaluation_basis.py), [decision/canonical evidence](../../services/research_decisions.py), [decision UI](../../frontend/src/components/research/CandidateDecision.tsx).
- [Service lifecycle](../../services/scanner_research_service.py), [worker](../../services/scanner_research_worker.py), [retention and backup](../../services/research_storage.py).

The audit and plan do not change engine arithmetic, trading settings, runtime databases or the installed feature code. They specify the next reviewable increments.
