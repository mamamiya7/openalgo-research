# OpenAlgo Research — complete trader journey map

**Design target, 10 September 2026.** This document specifies the intended product experience. It expands the earlier [journey review](JOURNEYS.md); it does not claim the new pages, states or asset adapters are implemented. Current delivery remains the [declared preview scope](STATUS.md): one to eight long NSE cash-equity CSV strategies, VectorBT/Optuna, and bounded optional Nautilus.

[Open the visual map](trader-journey-map.html). The maps are a product specification, not a running trading screen.

The [integrated delivery plan](DELIVERY_PLAN.md) maps every journey to current evidence, implementation dependencies and acceptance work. It owns the implementation sequence; this document owns the target experience.

The [implementation checkpoint](EXECUTION_STATUS.md) distinguishes the newly working library and result transitions from the remaining design target below.

The [Chartink end-to-end journey](CHARTINK_JOURNEY.md) connects the browser extension to these native pages, including automatic naming, field defaults, required user choices, price preparation, engine/search execution and saved decisions.

## 1. Product model and organizing principle

The user is researching a question: “Does this idea work, under which conditions, and is it worth retaining?” A backtest is one observation; an optimization study is one search. Neither is the whole research record.

**Library → Experiment → Setup version → Baseline / Studies → Candidates → Comparison → Validation → Decision.**

An experiment can start from existing signals, an existing saved setup, a supported strategy template, a previous trial, a portfolio composition, or imported research evidence. Signal-generation templates and imported engine code require distinct supported connectors; they are not implied by CSV support.

Use four working perspectives within this model:

- **Signal trader:** imported observations, execution rules, holding periods, costs and opportunity selection.
- **Strategy researcher:** rule/template variations, parameter search, sensitivity and separated evaluation.
- **Portfolio manager:** shared capital, combinations, concentration, correlated losses and allocation decisions.
- **Returning operator:** resume work, inspect newer data, compare versions, investigate a discrepancy, preserve and retire research.

These are views of one product, not separate applications or separate broker accounts.

## 2. What is saved in the library

| Object | Meaning | Save and version behavior |
| --- | --- | --- |
| Experiment | Named research question and its connected work | Autosaved title/notes/tags; contains multiple versions, studies and results |
| Source | Original signals, universe definition or supported strategy input | Original bytes and interpretation retained; replacement creates a new source version |
| Setup draft | Current editable rules, allocations, costs and research periods | Autosaved to the account; incomplete drafts are allowed; never described as a completed run |
| Setup version | Exact configuration accepted for a run | Immutable; new edits create a child version |
| Dataset snapshot | Exact native prices, metadata, sessions and eligible input observations used | Immutable reference to frozen evidence derived from Historify; refresh creates a new snapshot |
| Backtest result | A particular setup on particular data through a particular engine | Immutable report, trades, attribution and assumptions; annotations are separate |
| Optimization study | One defined search question, objective, ranges, data and execution identity | Real Optuna study/trial semantics; continuing compatible work adds a recorded execution segment |
| Trial | One proposal and outcome within a study | Preserve trial number, parameters, state and result mapping; repeated proposals remain visible as such |
| Candidate | A shortlisted exact configuration linked to its trial/result | Selection is saved; removal from shortlist does not delete evidence |
| Comparison | Named selection of aligned results plus comparison settings | Snapshot the selected versions and assumptions; changing the set creates/revises the comparison record |
| Validation plan/report | Periods, selection rules and an evaluation outcome | Plan frozen before evaluation; report records every period used for selection and testing |
| Decision | Retain, reject, revisit, or hand off a chosen configuration, with reason | Append-only decision history; a new decision supersedes rather than erases an old one |
| Execution segment | One attempt to prepare data or run/continue a calculation | Records progress, timing, interruption and recovery independently of the study’s research identity |

“Saved result,” “saved setup” and “saved study” must never be synonyms. A user may keep a setup whose latest test failed and retain a rejected study because it explains why an idea was abandoned.

### Library behavior

The default library groups **Experiments**. Tabs provide **Studies**, **Setups**, **Sources**, **Shortlist** and **Archived** without changing ownership. The Studies tab directly satisfies “show all my optimization studies,” across experiments.

Search title, instrument/universe and tags. Filter by asset/product, venue, frequency, strategy family, engine, research period, modified date and activity state. Advanced filters include data version, parent experiment and validation status. Avoid displaying raw identifiers in the main table.

Each experiment row shows name, market/universe, current activity, last edited time and chosen result if one exists. Each study row shows experiment, objective, trials completed/budget, state and last activity. “Reviewed” reflects a human review; it is not an automatic quality certificate or proof of profitability.

Useful actions: open, rename, tag, pin, duplicate setup, compare selected results, archive, export. Destructive deletion is separate from archive and must reveal dependent saved results. Sources or price snapshots referenced by retained evidence cannot be silently removed. A privacy-safe share/export excludes credentials and respects the price-data sharing rights of the source.

## 3. Page and navigation map

Only **Research library** and the current **Experiment** need persistent primary navigation. Deep analysis lives inside the experiment. Data issues and progress open in contextual drawers; they do not become a second mandatory wizard.

| ID / surface | Content and primary action | Next, back and persistence |
| --- | --- | --- |
| P0 Library | Experiment/study lists, saved setups, sources, shortlist, recent work. **New experiment** | Open exact record; filters, search and scroll position survive returning |
| P1 Experiment overview | Question, notes, versions, baseline, studies, shortlisted candidates, validation and decisions. **Continue research** | Opens last active task; breadcrumb returns to P0; timeline preserves lineage |
| P2 Setup | Inputs/universe; asset-aware rules; capital/allocations; research and reserved periods; costs. **Run baseline** or **Start study** | Autosave draft; back to P1 preserves edits; execution freezes a setup version |
| P3 Data review drawer | Resolved instruments, intervals, dates, coverage, estimated work; only material conflicts expanded. **Resolve & continue** when needed | Back to the same P2 field; successful automatic preparation needs no extra click |
| P4 Activity drawer | Data preparation/calculation stage, elapsed work, honest progress, queue; **Pause**, **Resume** or **Stop** according to state | Closing drawer or changing pages leaves the job running; reopening restores current state |
| P5 Backtest | Performance, benchmark when valid, drawdowns, trades, positions/exposure, strategy attribution, settings. **Optimize this**, **Adjust & test**, **Compare** | Trade detail returns to same chart/filter; a study-origin backtest links back to the same trial |
| P6 Optimization study | Overview/history; parameter importance; slice/contour/parallel coordinates; trials; saved shortlist. **Open backtest**, **Compare candidates**, **Continue study** | Filters/axes/zoom preserved; selecting a trial never mutates the study; changed search opens a child study |
| P7 Comparison | Baseline plus candidates, common metrics, aligned curves, costs/assumptions differences, period and eligibility differences. **Select for validation** | Remove/add candidates without losing their sources; back restores originating study or library selection |
| P8 Validation | Reserved-period evaluation, rolling windows, robustness/scenarios, leakage history and evidence. **Keep candidate** or **Revise** | Revision creates a setup/study version; validation evidence remains linked; failed hypothesis can be retained |
| P9 Portfolio composition | Existing setup versions/components, cash/allocations, net/gross exposure, concentration, contributions and joint results. **Test combination** | Uses the same P5/P6/P7/P8 views; return to a component without losing the basket |
| P10 Decision & handoff | Chosen setup/version, evidence summary, notes, retain/reject/revisit, named reusable setup and export | Return to experiment; optional paper/live integration is a separately supported boundary, never an automatic optimizer action |
| P11 Review over time | Explicit refresh/new-period runs, comparison against frozen baseline, paper/live observations if separately connected, revision/retirement | New data creates new evidence; scheduled review is opt-in; previous conclusions remain readable |

Proposed experiment tabs: **Overview · Setup · Backtests · Studies · Validation · Decisions**. Portfolio composition is a setup mode. Compare opens a dedicated view with a clear return location. Activity is always reachable, but healthy operation does not produce repeated notifications.

### Back and drill-down rules

- Breadcrumbs show **Library / Experiment / Study / Trial** or the corresponding result path; labels use names, not hashes.
- Browser Back returns to the previous location with filters, axes, selected period and scroll intact.
- Opening a trial’s backtest retains “Back to study · Trial N.” If its full result must be calculated, display that action and job; do not imply summary data is a full saved trade ledger.
- A chart point maps only to an actual trial. An unsampled contour region can prefill a new test, not open an invented result.
- Changing view controls never changes the experiment objective, source data or saved calculation.
- Zooming to a date range filters the investigation; it does not recalculate a standalone backtest for that range. **Test this period** creates a separate result with declared starting capital and position state.
- Edits from a completed result create a new draft labelled with its parent; the prior result stays exact.
- A library action on multiple results opens a comparison with that selection preserved. Leaving comparison does not remove shortlist membership.
- Multiple browser tabs use revision checks. A conflicting edit offers reload or save as another version; silent last-write-wins must not replace a chosen setup.

## 4. Complete journey catalogue

“Existing” below means the declared preview has a relevant working component. It does not imply the proposed end-to-end journey is already complete.

| Journey | Start → user action → destination | Automatic work and saved outcome | Exit / return and current coverage |
| --- | --- | --- | --- |
| J01 Capture an idea | P0 → new question → P1/P2 | Create experiment and autosaved draft with optional tags | Leave and reopen without running. Proposed |
| J02 Test existing signals | P2 → upload/reuse CSV → P5 | Parse observations, resolve identity, infer data, prepare Historify, freeze evidence and calculate | Edit parent setup or inspect trades. Existing core |
| J03 Build signals from rules | P2 → supported indicator/template → baseline | Generate signals causally; plan feature warmup and complete parameter range | Save template-based setup. Proposed connector |
| J04 Import outside research | P0 → import source/setup/report | Validate format, versions, original evidence and ownership; distinguish reproducible import from attachment | Missing data/runtime gives review-only state. Proposed |
| J05 Resolve ambiguous input | P3 → choose exact instrument, timezone or CSV meaning | Retain mapping decision; show preview before a consequential interpretation | Return to conflicting field; draft preserved. Partly existing |
| J06 Prepare missing data | P2 → run → P4 | Choose required streams, reuse native data, fetch only missing coverage, checkpoint and read back | Automatic completion to P5/P6; recover in place. Existing core |
| J07 Read a baseline | P5 → inspect performance | Use engine records, same capital basis, declared benchmark and observed coverage | Optimize, compare or revise. Existing summary; expanded analysis proposed |
| J08 Diagnose a loss | P5 chart → interval/trade → detail | Link candles, entry/exit, costs, rules, exclusions and contribution | Back to exact chart/filter. Proposed linking |
| J09 Adjust a known setup | P5 → Adjust & test → P2 | Create child draft and preserve original setup/result linkage | New baseline, compare with parent. Proposed transition |
| J10 Optimize settings | P5/P2 → choose axes/objective/budget → P6 | Validate full candidate universe, fixed cohort, real Optuna proposals and engine evaluation | Baseline-linked study. Search core exists |
| J11 Optimize allocations | P9 → allocation ranges/constraints → P6 | Preserve shared capital, feasible allocations and component versions | Compare combined risk and contributions. Existing core; comparison proposed |
| J12 Monitor a search | P6/P4 → inspect progress/history | Record completed, repeated, rejected, failed and active trials honestly | Navigate away or request pause. Progress exists; analysis/pause proposed |
| J13 Explore parameter behavior | P6 → importance/slice/contour/parallel views | Use actual study distributions/outcomes and supported analysis functions | Shortlist trial or prefill child search. Proposed |
| J14 Test a manual hypothesis | P6 → propose exact settings | Queue supported manual candidate with clear provenance; validate constraints | Trial → P5; no fabricated trial from chart interpolation. Proposed |
| J15 Continue an unchanged study | P6 → add budget | Append compatible execution segment; retain frozen objective/data/semantics | Same study, new segment and counts. Interrupted recovery exists; completed extension proposed |
| J16 Narrow or change the search | P6 → change ranges/objective/engine/data | Create child study; retain parent and reason. Reuse outcomes only when identity/comparability is proven | Return to parent study with views intact. Proposed |
| J17 Shortlist candidates | P6/P5 → keep candidate(s) | Save exact config/result links independent of current ranking | Cross-study shortlist, P7. Proposed |
| J18 Compare fairly | P7 → baseline/candidates | Align price period, capital/currency and eligibility; expose differences; calculate normalizations explicitly | Select candidate or return to investigation. Proposed |
| J19 Check untouched data | P8 → freeze candidate and reserved period | Separate selection from evaluation, apply same causal policies; track data already inspected | Keep/reject/revise. Fixed holdout core exists |
| J20 Walk forward | P8 → rolling/expanding schedule | Refit only inside each training window, evaluate next window, define position carry/reset and aggregate results | Inspect weak windows or new study. Proposed |
| J21 Stress assumptions | P8 → costs, slippage, delays, missingness, parameter neighborhood | Bounded declared scenarios, same engine; distinguish empirical from simulated perturbations | Compare degradation; record scenario recipe. Proposed |
| J22 Transfer across markets/periods | P2/P8 → alternate universe/venue/period | Resolve fresh instrument profiles and data; preserve rules and record changed assumptions | New child experiment or validation report. Proposed |
| J23 Investigate engine disagreement | P7 → supported VectorBT/Nautilus results | Check equal inputs and documented differing fill policies; reconcile at first differing event | No automatic engine “vote”; retain explanation. Proposed |
| J24 Combine strategies | P9 → saved component versions → joint backtest | Shared cash/margin/currencies only where supported; test simultaneous demands and attribution | Compare combination to defined standalone baselines. Existing cash core |
| J25 Review exposures and capacity | P5/P9 → concentration/liquidity/sizing | Derive supported exposures from actual positions; use quote/volume metadata where available | Revise sizing or portfolio; unavailable estimates stay unavailable. Proposed |
| J26 Recover interrupted work | P4 → reconnect/fix/resume | Preserve completed evidence, verify versions, resume from valid boundary | Same context and honest remaining work. Existing components |
| J27 Return days later | P0 → last experiment/study | Restore server-saved draft, view state and latest activity; no new data request | Continue, inspect or archive. Saved runs exist; full library proposed |
| J28 Retain or reject an idea | P10 → decision and reason | Save chosen version plus evidence; rejection retains useful negative results | Return to library; revisit later. Proposed |
| J29 Reproduce versus refresh | P5/P11 → Replay exact / Test newer data | Exact replay uses frozen evidence; refresh is a new dataset/result with a diff | Compare original/new versions. Replay exists; refresh journey proposed |
| J30 Export or share | P10/P0 → report/setup/evidence export | Choose reproducible bundle versus redacted report; dependencies and price rights handled | Original private account unchanged. Exact export core exists |
| J31 Review paper/live observations | P11 → explicitly connected execution observations | Match a frozen selected setup to later observed fills; attribute execution differences | Revise/retire through P10; activation is separate. Future connector |
| J32 Organize and retire | P0 → tags/archive/restore/deletion | Reference-aware retention; active work is stopped separately before archive/deletion | Undo archive; irreversible removal has impact preview. Proposed |
| J33 Upgrade or change broker | P0/P3 → continue research after change | Recheck capabilities/metadata/version compatibility; old results remain readable | New data/version for changed context; no silent price-source substitution. Partly existing |
| J34 Work across tabs/devices | Any view → reopen same experiment | Account-scoped server draft and optimistic version checks; no duplicate submissions | Resume view; resolve conflicting edits. Existing identity checks; expanded draft model proposed |

## 5. Pause, stop, save and resume

### Drafts, navigation and saving

Typing edits autosaves the draft after validation of structure, not validation of run eligibility. A small “Saved” indicator is enough; no success toast per field. Save failure shows “Not saved” with retry and keeps the local draft recoverable. Account logout removes private browser copies without deleting server evidence.

Running freezes a new setup version and its full accepted requirements. Parameters remain editable only in a new draft. A short note or research decision can be appended to a saved result without changing its original calculation.

Closing a browser tab is navigation, not cancellation. A bounded job keeps running while the installation and worker are available. Stopping the computer or worker may interrupt it; the UI must not promise continued background execution in that case.

### Job states and actions

| State | User sees | Allowed action / what is saved |
| --- | --- | --- |
| Draft | Setup, last saved time, unresolved required fields | Edit, leave, duplicate, run when valid |
| Queued | Waiting, position when known | Remove from queue; setup version remains |
| Preparing data | Reusing/downloading prices, truthful work units | Pause at a durable data checkpoint, or stop; fetched native candles and completed receipts remain |
| Running | Current stage/trials, elapsed and bounded budget | Pause request or stop request; navigate freely |
| Pause requested | Pausing after current safe unit | Do not claim paused before a checkpoint. Exact support depends on adapter |
| Paused | Saved checkpoint and last completed unit | Resume same identity, inspect completed trials, or end study |
| Needs attention | One actionable cause: auth, ambiguous symbol, quota, incompatible runtime | Resolve and return; retained progress is shown; no silent source or engine substitution |
| Interrupted | Worker/process stopped unexpectedly | Verify compatible identity and resume; rerun an incomplete trial only when the adapter cannot checkpoint it |
| Stopped early | User ended the current execution segment | Inspect completed trials; later continuation may add a new compatible segment |
| Completed | Budget/work complete and result published | Explore, compare, duplicate, validate; adding search budget is a recorded extension |
| Failed | Work cannot complete under present inputs | Preserve completed evidence; fix and resume only if identity remains valid, otherwise create a child version |
| Archived | Hidden from default library | Review and restore; this is an organizational state, not a running job state |

**Pause** means stop at the next supported safe boundary and retain continuation intent. **Stop** means end the current segment and keep completed work. **Discard draft** deletes only that unrun draft. Never label destructive data deletion “Cancel.”

For optimization, the default safe boundary is a completed trial. A pause request may wait for the current engine call; hard interruption preserves completed trials but does not promise an exact mid-simulation resume. Data preparation checkpoints at durable chunks. An engine may add finer checkpoints only through a tested contract.

Finite automatic retry/backoff is appropriate for classified transient failures and rate limits. Authentication, user-requested pauses, changed financial assumptions and unsupported data require distinct handling. Resume after reconnect restores the same job only when its evidence and versions still match. Repeated clicks cannot create duplicate runs.

Optuna’s documented stop behavior finishes running trials before leaving its optimization loop; our UI must represent that honestly. [Optuna Study API](https://optuna.readthedocs.io/en/v5.0.0/reference/generated/optuna.study.Study.html)

### Continue, fork, replay and refresh rules

| Change | Correct product action |
| --- | --- |
| More trials, same accepted search/data/objective/engine identity | Extend study with a recorded budget segment; adapter must support reproducible continuation |
| Narrow/widen ranges or change search algorithm | Child study by default; retain parent, reason and reuse policy |
| Change execution rules, costs, objective, eligible cohort, data version or engine | New setup/study identity; previous outcomes cannot be mixed into the score history as equivalent |
| Inspect different chart axes or another displayed metric | View-only change; original objective and ranking remain recorded |
| Reproduce a selected result | Exact replay with the saved configuration, data and compatible runtime |
| Fetch newer history or correct data | New snapshot and child result, with input differences retained |
| Edit a note, tag or shortlist | Save metadata independently; calculation evidence stays immutable |

## 6. Native engine analysis, kept in context

**VectorBT backtest:** account equity/cash, supported risk/return metrics, drawdown depth/duration, positions and exposure, costs, trades, grouped contributions and a declared benchmark. The chart can drive a filtered trade investigation; an inspected trade can open its price context. Additional metrics appear in the relevant tab rather than all in the overview.

**Optuna study:** history, objective and distributions, actual trial states, importance, slices, contour, parallel coordinates, ranked trials and saved shortlist. Advanced plots include EDF/rank, timing only when recorded, intermediate learning curves only when the evaluation supplies meaningful intermediate data, and Pareto views only for a multi-objective study.

Reuse official engine APIs and figures. The Optuna analysis adapter must use the real trial history, including repeats and rejected proposals; a deduplicated winner table is not the study. Saved summaries do not automatically contain full VectorBT object analysis. Retain or reconstruct compatible engine-native results through a safe, explicit evidence contract.

A plotted strong region suggests additional tests; it is not an observed tradeable outcome. “Parameter importance” describes the sampled study and evaluator. Constraints, sample size, correlated parameters and search space affect it; the UI supplies concise context only when material.

The documented [Optuna visualization APIs](https://optuna.readthedocs.io/en/v5.0.0/tutorial/10_key_features/005_visualization.html), [VectorBT portfolio API](https://raw.githubusercontent.com/polakowo/vectorbt/v0.28.5/vectorbt/portfolio/base.py) and [VectorBT walk-forward example](https://github.com/polakowo/vectorbt/blob/v0.28.5/examples/WalkForwardOptimization.ipynb) are reuse points. None removes our responsibility for broker data, causal rules and comparable evidence.

## 7. User choices versus automatic reasoning

The app resolves facts, calculates consequences and remembers accepted preferences. It must not invent economic assumptions to make a run succeed.

| Category | Automatic behavior | User responsibility |
| --- | --- | --- |
| Instrument identity | Infer asset/venue/product from an exact native instrument identifier; show one concise resolution | Resolve an ambiguous symbol or choose the actual exposure when the input names only an underlying |
| Broker and price store | Use the existing connected broker and native Historify; reuse matching coverage; download missing required windows | Connect/authenticate broker when required; approve a genuinely different data source rather than receiving a silent fallback |
| Data resolution | Derive required bars/quotes/events from observations, execution rules, features and the complete optimizer range | Choose the intended strategy clock and execution behavior, not a technical candle database |
| Calendar and dates | Resolve effective sessions/timezone/DST/expiry where authoritative; account for lookbacks and holding tails | Choose research period and reserve evaluation periods before using them for selection |
| Sizing and capital | Apply lot/multiplier/currency rules and feasibility checks; summarize actual resulting quantities | Choose capital, exposure/allocation intent and risk limits; no automatic leverage increase |
| Costs | Prefill an accepted broker/venue fee schedule or named conservative template with effective date | Review unknown/hypothetical slippage, borrow, funding, tax assumptions and fill policy |
| Engine | Filter to adapters that support the complete request; retain a compatible default and expose More settings | Select a different compatible simulator if desired; unsupported requests cannot be silently simplified |
| Optimization | Build valid ranges, constraints and full data requirements; suggest bounded work estimates | Choose what may vary, objective/tradeoff and budget; changing them is a new research decision |
| Comparison | Detect unmatched data/capital/currency/period/cohort/policy and display differences | Decide whether comparison remains meaningful; normalization is explicit |
| Saving | Autosave drafts/view state, freeze executed versions, preserve original evidence and relationships | Name/organize research and record retain/reject/revisit decisions |

Resolution comes from **instrument profile + venue + effective date + broker capability + strategy rules + engine adapter**. A class label such as “options” is insufficient.

No automatic engine fallback, future-informed universe, changed contract, inferred settlement price, fabricated quote, universal zero funding/borrow, or silent conversion of shares to lots.

## 8. Common setup and asset-aware questions

The first setup asks for the research idea/input, instruments or universe, capital and rule intent. Most contract facts are inferred. An advanced drawer holds assumptions; an unresolved consequential fact brings only the relevant question into view.

For every asset/product profile, plan:

- exact instrument identity and historical universe membership;
- timezone, trading/valuation sessions and instrument lifecycle;
- observation timing, earliest causal order, order/fill model and intrabar ambiguity;
- quantity unit, minimum increment, tick value/multiplier and currency;
- cash/margin/leverage, exposure netting/hedging and settlement/collateral behavior;
- trading fees, financing/funding/borrow and corporate/cash-flow events where applicable;
- full required historical streams, including features and optimizer maximum requirements;
- expired/delisted data availability, missingness and replay provenance;
- engine/adapter support before any expensive download or trial allocation.

The following asset matrix describes the target product. The current long-NSE-cash adapter cannot be enabled for another asset simply by adding a dropdown.

| Profile | User selects | App resolves automatically | Contextual result view |
| --- | --- | --- | --- |
| Cash equities / ETFs / listed funds | Universe, direction, allocation, holding and dividend policy | Exact security, sessions, native bars, corporate-action facts where available | Performance, trades, distributions, concentration |
| Equity shorts / long-short | Intraday or borrowed holding, exposure limits, borrowing policy | Product availability and authoritative borrow/financing inputs where available | Gross/net exposure, borrow costs, recalls/forced exits |
| Equity/index options / multi-leg | Underlying, structure/legs, expiry/DTE, strike/delta rule, roll/hedge/expiry behavior | Dated contracts, deliverable, multiplier, exercise style, settlement, required chain and all candidate legs | Legs, Greeks, package trades, expiry/assignment, margin |
| Financial futures | Exact expiry or roll rule, lots, collateral/holding policy | Actual historical contracts, multiplier, tick value, settlements, roll overlap | Contract and roll trades, MTM, margin |
| Currency derivatives | Pair, venue, derivative and expiry/roll, account currency | Contract/quotation and settlement currency, calendars, conversion requirements | FX attribution, rolls and settlement |
| Spot FX / CFDs | Actual broker product, units, execution window, financing/margin model | Broker quotes, tick/units, FX conversions and available financing schedule | Spread, financing, currency exposure, stop-out |
| Commodity derivatives | Exact contract variant, units, roll and delivery-avoidance rule | Dated specifications, evening sessions, tender/delivery/expiry, settlement | Rolls, delivery events/exclusions, collateral |
| Crypto spot | Venue/pair, sizing, reporting currency, cash versus margin | Base/quote, steps/minimum notional, venue data and fee currency | Trading PnL, currency/fee attribution |
| Crypto perpetuals / dated / options | Linear/inverse product, collateral, leverage/account model, funding/roll/legs | Contract terms and available funding, mark/index, chain and settlement streams | Funding, collateral, liquidation, legs |
| Direct bonds / bills | Security, face value, cashflow/reinvestment strategy | Dated coupon/maturity/day-count/accrual conventions and events | Clean/dirty price, cashflows, accrued interest, rate risk |
| NAV funds / SIP | Exact scheme/plan/option, contribution/redemption schedule | NAV history, applicable dates and dated scheme rules where available | Contributions, corpus, money/time-weighted returns |

All rows specify target journeys. Direct fixed income, NAV funds, bespoke OTC products, physical inventory and on-chain strategies remain separate future profiles until their data and calculation contracts exist. Indices and synthetic signal series are not automatically tradable instruments.

The full [Asset profiles](ASSET_PROFILES.md) appendix defines selections, automatic requirements, material assumptions, data requirements, failure states and primary references for each family. Current Historify OHLC cannot silently serve as an options-chain, funding, corporate-action or bond-cashflow store; additional data types require explicit native OpenAlgo extensions.

## 9. Data, bias and professional research decisions

**Research periods are a resource.** Reserve evaluation periods before baseline selection, search or repeated candidate inspection uses them. Record which periods were used for which decision. Reusing a holdout to choose a winner makes it part of selection; future validation needs another reserved period or an explicitly exploratory label.

**Universe history matters.** Current surviving symbols are not a historical index membership list. Delisted securities, changed contract specifications, adjustments, futures rolls, options-chain availability and publication timestamps can change results. The data review exposes a material limitation and preserves the chosen policy.

**Fair comparison needs a contract.** Baseline/candidates should use aligned periods, currency/capital, coverage cohort, fees and execution rules. A comparison may intentionally differ on one assumption; that is a labelled experiment, not an unqualified improvement. Portfolio contribution, a standalone strategy run, and the portfolio rerun without that strategy answer different questions. Removing a component can free capital and change other fills; its effect requires a new joint calculation.

**Portfolio clocks and accounts matter.** Mixed calendars/currencies and derivative margin require compatible event timing, FX conversion and account semantics. Reusing the current finest-interval cash-equity planner does not establish a multi-asset margin engine. Standalone simulations are an explicitly labelled fallback only if the user selects them.

**Robustness is a workflow, not a green badge.** Report results across separated periods, perturbations and combinations; record poor outcomes and rejected hypotheses. No automated “ready to trade” label follows merely from positive PnL or a winning trial.

**Review after selection is optional and explicit.** Future scheduled research or paper/live comparisons require a chosen cadence, data scope and destination. They do not run because someone saved a candidate. Research handoff alone does not place an order.

## 10. Product states that need deliberate design

| Situation | Product response | What remains usable |
| --- | --- | --- |
| Broker token expires during acquisition | Keep completed native data/checkpoints; ask to reconnect in the existing broker flow; return to the paused task | Library, saved reports and complete cached inputs |
| Broker returns a successful empty window | Apply declared eligibility policy across the experiment; show affected observations and reasons | Valid retained cohort; all-unusable input must not become a zero-return success |
| Historical contract or critical settlement data missing | Identify the exact missing requirement before engine execution | Draft, earlier results and alternative supported date/product selection |
| Too much requested work/storage | Estimate before admission, provide a smaller scope/budget action; retain draft; reference-aware storage management | Completed evidence; no automatic deletion to make space |
| Trial rejected/failed | Preserve state/reason; exclude it from valid-score selection; stop repeated systemic failures | Completed valid trials and study history |
| User changes broker or installs a new engine version | Recheck capabilities; exact old evidence remains accessible; replay checks its runtime identity | Reports and exports; incompatible replay is not silently recalculated |
| Comparison uses different prices or assumptions | Show specific mismatches and offer matched reruns as new evidence | Original comparison and original results |
| Simulation and broker fills disagree | Compare quantities/timestamps/costs/venue policy; preserve both sources | Research evidence and observed execution record as distinct facts |
| Library item is referenced elsewhere | Archive normally; destructive deletion shows dependencies and blocks breaking retained reports | Dependent results and restore from archive |
| Same draft edited elsewhere | Detect version conflict, let user keep both versions or reload | Neither accepted setup is silently lost |

## 11. Capability map and build order

| Layer | Existing foundation | Work required for the target |
| --- | --- | --- |
| OpenAlgo integration | Account, broker history, Historify, job store/worker | Rich historical instrument profiles, supported extra data streams, all-broker acceptance |
| Research organization | Saved sources/results and account-scoped browser draft | Server drafts, experiments, immutable setup versions, study library, lineage, shortlist and decisions |
| VectorBT/Nautilus adapters | Declared joint cash-equity execution | Expose native analysis; add each asset/product only with tested financial/data contracts |
| Optuna | TPE/grid, numerical ranges, exact checkpoint replay | Study exploration, trial/result links, compatible study extension, manual candidates, conditional/categorical or multi-objective search where explicitly supported |
| Workflow | Upload, run, basic results, fixed holdout, replay | Complete result→edit→optimize→compare→validate→save transitions and honest pause behavior |
| Ongoing use | Saved evidence and exact exports | Version-aware refresh, review/retirement, optional scheduled or execution-observation connectors |

**Build slice 1 — finish the existing asset journey.** Experiment/study library, server drafts/versions, completed-result actions, native study exploration, shortlist/comparison, pause semantics and reusable setups for the current cash-equity scope.

**Build slice 2 — deepen decisions.** Expanded trade diagnosis, benchmark, compatible study continuation, separated validation and rolling/scenario analysis, portfolio comparisons and decision records.

**Build slice 3 — add product profiles.** Admit one new instrument family at a time with real metadata/data examples, hand-checkable execution, saved evidence and full UI journeys. Asset-class selection alone is not acceptance.

**Build slice 4 — ongoing research connectors.** Explicit periodic review, observed-execution comparison and supported handoff. User authorization and product scope determine any trading action.

This order is a proposed implementation sequence. It does not retract the comprehensive target or promise every asset class in the present release.

## 12. Acceptance scenarios for the complete experience

1. **First CSV research:** upload daily signals, reserve a later period, run using native daily prices, understand performance, optimize from the baseline, compare candidates, validate, save a version.
2. **Intraday research:** timed signals and clock/holding rules automatically select required minute data; partial coverage cannot turn missing exits into invented fills.
3. **Long search interruption:** leave browser, return, request pause, resume, then add compatible budget; completed trial identity and earlier rankings remain auditable.
4. **Changed hypothesis:** narrow ranges, change costs or change objective; a child study records the change and keeps the original result.
5. **Chart-to-result:** select an actual Optuna trial, inspect its settings and full backtest, then return to the same filtered plot; unsampled points cannot masquerade as trials.
6. **Portfolio decision:** combine saved strategy versions, inspect shared-capital constraints and attribution, compare allocations and make a recorded decision.
7. **Derivative profile:** resolve an actual dated contract, validate quantity/margin/expiry/settlement data and selected execution model before running; missing authoritative requirements stop preparation clearly.
8. **Return after an update:** reopen old reports without acquisition, replay only with compatible identity, refresh into a new result and inspect differences.
9. **Multi-tab save conflict:** keep both draft versions without duplicate execution or silently replacing accepted settings.
10. **Retirement and export:** archive and restore a failed idea; export a selected setup/report with evidence dependencies and privacy handled explicitly.

Every scenario is complete only when the user can perform it through the application and reopen what was saved. Test counts or engine imports are supporting evidence, not substitutes.

## 13. Evidence and design boundaries

The existing implementation was inspected through Graphify source locations and the current React/service/connector files referenced in [JOURNEYS.md](JOURNEYS.md). The design also uses official engine and instrument references supplied in the asset appendix. No live broker actions, orders, scheduled research or app behavior changes were performed for this map.

The current preview supports a narrower market/execution scope than the target described here. “Automatic” means resolving authoritative facts or applying an accepted rule. When neither exists, preserve the draft and request the specific missing choice.

See [Asset profiles](ASSET_PROFILES.md) for official venue/instrument references and [Journey review](JOURNEYS.md) for current source paths. General engine documentation is a design reference; acceptance must use the pinned runtime and actual adapter.
