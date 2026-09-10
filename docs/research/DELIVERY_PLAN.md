# OpenAlgo Research — integrated delivery plan

**Planning baseline: 10 September 2026, source revision `94d1043fa6f7b1a75cec22ca0a4fd093e288464b`.** This plan connects the [intended journeys](PRODUCT_JOURNEY_MAP.md), [asset profiles](ASSET_PROFILES.md), [source review](JOURNEYS.md) and [recorded delivery status](STATUS.md). It is the implementation sequence and acceptance contract; it does not mark proposed features as shipped.

Execution has started. The [implementation checkpoint](EXECUTION_STATUS.md)
records the working library/draft/version increment and result-to-study links,
with explicit remaining work and evidence boundaries.

The outcome is one installable OpenAlgo Research distribution in which a person can develop a research idea, test it using the connected broker's native historical data, understand and improve it through established engines, make a supported decision, and return to the saved work. The existing distribution remains the delivery vehicle. No second research account, general calculation engine or broker downloader is planned.

## 1. Delivery decisions

1. **Finish one whole research loop before widening the market menu.** Make the current daily/intraday cash-equity workflow complete first. Other instrument families are explicit work packages with the same end-to-end acceptance, not forgotten scope.
2. **Make the Library and Experiment the organizing layer.** Backtests, actual Optuna studies, candidates, comparisons and decisions belong to an experiment. Preserve existing run/evidence identities below it.
3. **Reuse native OpenAlgo and engine features.** OpenAlgo supplies account, broker services and Historify; VectorBT/Nautilus calculate; Optuna searches and supplies analysis. Our work is requirements translation, adapters, persistence, orchestration and native presentation.
4. **Select data from the entire accepted request.** Use signal timing, execution rules, lookbacks, all allowed search values, contract history and engine requirements. Reuse matching native data, fetch missing coverage once, freeze inputs, then calculate. Material unresolved intent is a specific question; it is not guessed into a different financial experiment.
5. **Retain exact history while enabling change.** A view change stays a view change. Edited assumptions create a child version. A new data download creates new evidence. An unchanged study can receive another recorded execution budget once that continuation contract is implemented.
6. **Keep healthy UI quiet.** Relevant fields, one primary action per context, focused result tabs and contextual drawers. No new dashboard of every diagnostic, metric or engine option.
7. **Validate the claimed combination.** Asset, venue, broker/history capability, engine, runtime and interval all contribute to support. A supported library or installed broker plugin alone does not establish that combination.
8. **Release in usable increments.** The first complete public research milestone covers the existing cash-equity scope. Advanced research, each asset family and ongoing review follow as named increments through the same release checks.

No reliable full-product calendar date is assigned before the runtime baseline and the first new asset data probe. Work below is ordered and bounded by demonstrable outcomes; unsupported historical data and access to another broker account are explicit dependencies, not hidden engineering estimates.

## 2. What exists, what is missing, and how we will verify it

Evidence labels:

- **C — code-backed:** inspected relevant source; not a fresh browser acceptance claim.
- **H — historical acceptance:** a bounded previous check recorded in STATUS/VERIFICATION; retain its version, platform and data-source limits.
- **O — observed now:** a fresh end-to-end receipt with version, input and runtime. No new O evidence is claimed by this planning task.
- **P — proposed:** required behavior that has not been accepted.

| Area | Current foundation | Gap to target | Evidence / next proof |
| --- | --- | --- | --- |
| Input and calculation | CSV sources, one to eight long NSE cash strategies, shared-cash VectorBT, optional bounded Nautilus | Reusable editable setup versions and other supported input/profile types | C/H; M0 repeats the current visible daily and timed-input paths |
| Market data | Daily/minute requirement planning, native Historify reuse, missing-only connected-broker acquisition, frozen snapshots | General instrument/venue profiles, richer history streams, broader broker acceptance | C/H for bounded Fyers cases; M0 and M6 produce separate controlled and real-source receipts |
| Research records | Sources, jobs, `ResearchExperiment`, `ResearchAttempt`, receipts, evidence history and exports | Named multi-study experiment container, server drafts, library queries, shortlist and decisions | C; M1 evolves existing records and proves populated migration |
| Results | Summary, equity, contribution, trades/settings/trials, exact replay/export | Completed-result → edit/optimize, deeper diagnosis and comparisons | C/H for current paths; M2/M4 add and exercise the missing transitions |
| Trial evidence | Full completed/pruned proposal checkpoint records including repeated proposals; separate deduplicated ranked rows | Complete native analysis contract and actual trial navigation; recorded timing/failure/active state where available | C; M3 must expose original proposals rather than synthesize trials from rankings |
| Continuation | Version-bound JSON replay and interrupted-job recovery | Pause UX and compatible extension of a completed study | C/H for recovery; current identity includes total trial budget, so M3 splits scientific identity from execution budget |
| Validation | Fixed earlier/later calculation split by unique signal dates; persisted evidence-use history | Candidate-driven reserved evaluation, history integration, rolling/scenario analysis and decision records | C/H for fixed split; history warning path currently belongs to legacy research, so M2/M4 connect it to the portfolio flow |
| Interface | Native React, shadcn-style primitives, basic saved runs and account-keyed sessionStorage draft | Experiment/library navigation, server save, preserved view state, fewer duplicated controls | C/H bounded current journeys; M0 captures actual screens and M1–M4 check them |
| Distribution | Versioned native OpenAlgo bundle, locked engines, installer/checks, optional Linux runtime | New-schema upgrades, broader host-version compatibility, full Docker/restore and unaided use | H bounded Windows/Linux installs; R checks the exact new release artifact |
| Extended scope | Upstream OpenAlgo tools and engine capabilities exist | Signal templates, broader instruments, advanced search, research import and execution observation connectors | P for the new joint research journeys; M5–M7 validate reuse before enabling controls |

Authoritative source entry points: [metadata](../../database/research_db.py), [portfolio page](../../frontend/src/pages/PortfolioResearch.tsx), [builder](../../frontend/src/components/research/PortfolioBuilder.tsx), [results](../../frontend/src/components/research/PortfolioResults.tsx), [portfolio contract](../../research/portfolio.py), [coordinator](../../services/research_portfolio.py), [Optuna adapter](../../research/connectors/optuna_portfolio.py), [native archive boundary](../../services/research_historify.py), [service/job lifecycle](../../services/scanner_research_service.py), [worker](../../services/scanner_research_worker.py).

The legacy connector registry and scanner screens describe older workflows. Do not use their restrictions to infer the new portfolio adapter's capabilities, or their analysis functions to claim a new portfolio journey is complete.

### M0 baseline procedure

Run a bounded representative set first: daily CSV, timed CSV, mixed strategies, optimize, selected-trial replay, interrupted acquisition/calculation, reopen after restart, and a legacy saved report. Capture screen/navigation behavior, source identity, data requirements and saved evidence. Then classify all J01–J34 against the observed components: working, partial, absent or not yet observed. Do not attempt an unsupported derivative run just to fill a checklist.

Use controlled isolated data for reproducible development. Real-broker checks are separate and use the authorized installation/account context; preserve credentials and live databases in place. Main-app validation uses the user's normal installation when authorized and the changes are reviewable. These tests must not execute existing scheduled strategies, change trading mode or place orders.

## 3. Architecture and data changes

| Boundary | Implementation decision | Completion proof |
| --- | --- | --- |
| Frontend | Evolve native Research routes; retain old saved-job links. Add experiment context and focused subviews using existing API client, query state and local UI primitives | Direct URLs, refresh and browser Back restore the exact saved item and view |
| Product metadata | Add a named research container, versioned drafts/setups, links, shortlist/comparison/decision records using native SQLAlchemy patterns | Account scoping, optimistic revisions, idempotent submissions and reference checks work on fresh/populated stores |
| Existing evidence | Keep `ResearchExperiment` job-based identity/specification/checkpoints and attempt relationships intact; link them to the new product container | No old result hash, price snapshot or execution policy is rewritten |
| Study persistence | Use a managed Optuna RDB storage adapter for new studies under the research data directory; default local SQLite with one calculation writer and bounded reads | Locking, session cleanup, restart, interrupted trial reconciliation and actual Trial mapping pass on Windows and eventlet production |
| Study compatibility | Retain legacy JSON recovery readers; adapt historical proposal records for read-only analysis when sufficient. Use a versioned scientific identity plus execution segments for new studies | More budget can continue a compatible study; changing objective/ranges/data/policy creates a child |
| Sampler recovery | Preserve deterministic proposal reconstruction/version checks or an explicitly safe version-bound state contract; RDB rows alone do not preserve sampler state | Stop/resume and uninterrupted seeded runs produce the promised proposal/result sequence; no untrusted pickle import |
| Native prices | Historify stays the mutable native candle archive; research snapshots remain immutable evidence. Generalize existing NSE assumptions only through admitted instrument profiles | Resolved venue/interval, native write/readback, coverage and provenance agree |
| Other historical streams | Extend native OpenAlgo data services/store contracts for admitted corporate events, chain/quotes, funding, settlement or FX inputs | Each required stream has source, effective time, availability, version and replay coverage |
| Worker | Keep expensive simulations and plots outside the broker-facing web process; retain leases, bounded queues, checkpoints and atomic publication | Pause/restart/timeout leave no duplicate worker, partial accepted result or leaked owned resource |
| Engine adapters | Expose native supported statistics/figures and preserve execution differences; add asset/account types through explicit contracts | Hand-checkable fills, cash/margin/cashflows and attribution reconcile |
| Host compatibility | Version host hooks, schemas, adapter contracts and dependency locks; test the selected OpenAlgo update before support is declared | Fresh install, populated upgrade, restore and old-report access on the released artifact |

Optuna documents persistent studies with an RDB backend and separately notes that sampler/pruner state is not stored there. The recovery design must account for both. [Optuna persistence](https://optuna.readthedocs.io/en/v5.0.0/tutorial/20_recipes/001_rdb.html)

The local storage choice is for the existing bounded single-worker design. Multi-worker/distributed optimization is a distinct future capability; it cannot be enabled by raising concurrency against SQLite. [Optuna concurrency guidance](https://optuna.readthedocs.io/en/v5.0.0/faq.html#how-can-i-solve-the-error-that-occurs-when-performing-parallel-optimization-with-sqlite3)

Official Optuna figure APIs are the default presentation reuse point. An embedded Dashboard remains an optional integration after storage/authentication compatibility; it is not required for the complete native journey and its private React internals are not our public SDK dependency.

## 4. Ordered delivery packages

Each package includes service/UI changes, appropriate numerical or persistence tests, a browser journey and documentation. “Backend done” alone does not close it.

### M0 — establish actual behavior and preserve the baseline

**Depends on:** current preview. **Owner boundary:** acceptance fixtures and source/behavior inventory.

- Record current route, source and result behavior against J01–J34.
- Establish small daily, minute and mixed-strategy reference inputs with hand-checkable results and separate private real-broker receipts.
- Identify data, accounting, lost-work or misleading-success defects; fix those before feature expansion.
- Inventory old report formats and supported versions for migration/replay.
- Start a bounded feasibility inventory for every asset branch: available native history, historical contract facts and established-engine support. Investigate options-chain, borrow, funding, NAV and bond-history gaps before committing their detailed implementation; the complete cash journey proceeds while those probes run.

**Done when:** the actual-behavior column has evidence or an explicit unknown, the bounded baseline runs, and no critical incorrect-result/lost-work issue remains unresolved for the next slice. This is the prerequisite audit, not a claim that all target journeys already work.

### M1 — library, server drafts and versions

**Depends on:** M0. **Pages:** P0, P1, P2. **Owner boundary:** metadata/services and native navigation.

- M1.1 Add product-container and relationship metadata around existing sources/jobs/evidence; support legacy links without rewriting results.
- M1.2 Add account-scoped server drafts, optimistic revision conflicts, saved setup versions and idempotent launch.
- M1.3 Build Library views for Experiments, Studies, Setups, Sources, Shortlist and Archived; search/filter, rename/tag/pin and reopen.
- M1.4 Build Experiment overview and restore last context; connect existing raw source reuse and existing portfolio composition.
- M1.5 Implement archive/restore and reference-aware deletion impact; preserve active work and dependents.

**Done when:** create an unfinished idea, leave, reopen on another authenticated session, resolve a multi-tab edit conflict, run a frozen version, archive/restore it and still open an old saved report. This is the first usable increment.

### M2 — connect setup, data and backtest

**Depends on:** M1. **Pages:** P2–P5, P9. **Owner boundary:** portfolio coordinator and result/setup UI.

- M2.1 Add **Adjust & test**, **Optimize this**, **Save setup** and explicit **Replay exact** from completed results. Preserve all strategies, source IDs, allocations, selected settings and baseline identity.
- Rehydrate source display records as well as saved configuration; revealing the existing noncompleted-job edit button alone does not restore the complete setup.
- M2.2 Make full request inference/preflight a shared contract; expose only material ambiguity or missing requirements in Data review.
- M2.3 Reserve evaluation periods before selection/baseline use, connect existing evidence-use history to the new portfolio journey, and record period intent in immutable versions.
- M2.4 Improve backtest drill-down: linked chart/trade details, drawdowns, supported benchmarks, costs and actual exposure. View zoom does not rerun a shorter period.
- M2.5 Separate source selection from acquisition and exact replay from data refresh. Add bounded estimates and actionable recovery at the affected context.
- **M2.6 — next priority from user acceptance:** automatically continue bounded price-download batches and provide a compact animated **CSV → Prices → Optimize/Backtest → Results** view. Show actual CSV signals/unique symbols, required/cached/downloaded candles, then actual Optuna trial progress. Follow the counting, recovery, motion and acceptance contract in [run progress](RUN_PROGRESS.md). This work precedes richer study charts.

**Done when:** two saved CSV strategies flow to a baseline, an edited child and a baseline-linked optimization without copied settings or extra daily-to-minute downloads; returning restores the chart/trial context and old results remain unchanged.

### M3 — native study exploration and lifecycle

**Depends on:** M1/M2; storage/recovery proof precedes new continuation controls. **Pages:** P4/P6 with P5 links.

- M3.1 Implement the new study/trial storage and identity contracts; expose actual original proposal records, repeated/rejected/failed states and result links.
- M3.2 Add Overview/history, Parameters (importance, slice, contour, parallel coordinates), Trials and shortlist. Render supported native figures; keep advanced views contextual and load chart traces on demand.
- M3.3 Add pause-requested/paused/resume/stop states at durable boundaries, explicit execution segments, restart reconciliation and truthful progress.
- M3.4 Add compatible extra budget, child-search refinement and bounded manually proposed trials. Changed ranges/objective/data/engine do not mix with the original score history.
- M3.5 Preserve filters, axes and return path when opening a full trial backtest. Missing full ledgers require an explicit calculation job; unsampled contour coordinates prefill a test.

**Done when:** explore an actual study, inspect a repeated/rejected trial correctly, shortlist two candidates, open their exact backtests, return to the plot, pause/restart/resume and extend compatible work without lost or duplicated accepted trials. Sparse studies and old reports show supported views only.

### M4a — compare, validate and make a decision

**Depends on:** M2/M3. **Pages:** P7/P8/P10. **Owner boundary:** evidence comparison and validation orchestration.

- M4a.1 Compare baseline and shortlist with explicit period, cohort, capital/currency, cost and policy differences.
- M4a.2 Launch reserved-period evaluation from a frozen candidate; expose selection/evaluation history and prevent a reused selection period being presented as untouched.
- Record known in-app data use and user-declared outside research separately. The application cannot prove that a person has never examined a period elsewhere.
- M4a.3 Save retain/reject/revisit decisions, notes and a named chosen setup with its evidence.
- M4a.4 Produce a readable report and exact reproducible bundle; preserve dependencies and separate private market data from a shareable report.

**Done when:** upload/reuse → baseline → optimize → investigate → compare → reserved validation → retain/reject → leave/reopen works unaided through the app. **M0–M4a plus R is the first complete public research release for the declared cash-equity scope.**

### M4b — professional robustness and portfolio decisions

**Depends on:** M4a. **Pages:** P5/P7/P8/P9.

- M4b.1 Rolling/expanding walk-forward with causal fitting, declared reset/carry behavior and per-window results.
- M4b.2 Cost/slippage/delay, parameter-neighborhood and supported resampling scenarios with frozen scenario recipes and bounded work.
- M4b.3 Saved-component composition, allocation comparisons, correlated losses/concentration, actual exposure and supported capacity estimates.
- M4b.4 Separate contribution, standalone simulation and portfolio-without-component counterfactuals; the latter is a new joint simulation.
- M4b.5 Investigate supported VectorBT/Nautilus disagreement at the first differing event and recorded execution policy.

**Done when:** a trader can investigate a weak window, test assumptions or a changed composition, explain a material engine difference, and retain the evidence even when the idea is rejected.

### M5 — more ways to create and explore research

**Depends on:** M2/M3; M4a for selection outcomes. **Pages:** P0/P2/P6/P8.

- M5.1 Add a small connector-backed catalog of causal indicator/rule templates using established signal APIs; include warmup and search-wide data requirements.
- M5.2 Add bounded conditional/categorical search and true multi-objective studies only with corresponding constraints, trial/figure support and validation. EDF/rank, timeline and intermediate-value views depend on real recorded data.
- M5.3 Import versioned supported sources/setups/evidence; distinguish reproducible imports from report attachments. No generic arbitrary-code importer is implied.
- M5.4 Create transfer experiments across supported universes/periods, preserving changed profile and data assumptions.

**Done when:** a supported template can complete the same loop as CSV input, a conditional/multi-objective study retains correct trial semantics, and an import either reproduces its declared evidence or is clearly review-only.

### M6 — instrument platform and asset admission

**Depends on:** M2 request/data contract; M3/M4a for full journey acceptance. Discovery/probes can start after M0. **Pages:** contextual P2/P3/P5/P8/P9.

- M6.1 Implement effective-dated instrument profiles and capability decisions across venue, broker, interval, historical streams, account and engine.
- M6.2 Generalize native history access, calendars, universe/contract history, quantity/currency and missing-data rules beyond hard-coded NSE cash assumptions.
- M6.3 Add native historical stream contracts where needed; do not create a separate private research broker downloader.
- M6.4 Implement each profile in the admission sequence below, including contextual UI and numeric examples.
- M6.5 Maintain a tested broker/profile matrix and repeat acquisition/reuse/recovery tests for each claim.

**Done per profile:** exact instrument selected → required history resolved/acquired natively → engine calculation reconciled → study/comparison/validation → saved/reopened/exported evidence. Unsupported combinations remain unavailable with a specific reason, not silently simplified.

### M7 — return, refresh and connected review

**Depends on:** M1/M4a; M6 for any extended product. **Pages:** P0/P10/P11.

- M7.1 **Test newer data** creates a new snapshot/result and a diff against the frozen original; upgrading a broker/engine never rewrites history.
- M7.2 Opt-in periodic research review with bounded work, deduplicated scheduling, pause/resume, actionable notifications and visible data scope.
- M7.3 Import explicitly connected paper/live observations for comparison against a selected setup; preserve actual fills separately from simulation.
- M7.4 Versioned handoff definitions and integration adapters; connect to existing execution surfaces only for a separately supported workflow. Saving or optimizing never activates trading.
- M7.5 Review/retirement and reference-aware storage management across repeated runs.

**Done when:** revisit a chosen idea, refresh and explain changes, pause a scheduled review, compare observed execution when connected, and retire work without breaking older decisions. Live order activation is not part of this research completion gate.

### R — installation, compatibility and release acceptance

**Starts at M0 and accompanies every milestone.** This is implementation work, not a final documentation-only phase.

- R.1 Fresh Windows/Linux installation and supported Docker startup, worker health, restart, backup and restore using the built release artifact. Make the supported launch process understandable; expose unavailable worker state before accepting expensive work.
- R.2 Idempotent populated migration from the previous preview, interrupted migration recovery, supported host/engine update and matching-version rollback/restore. Test old saved reports and exact replay compatibility.
- R.3 Native API ownership/session checks, bounded request/figure/export sizes, retry idempotency, worker exclusivity, storage capacity behavior and resource cleanup. Run applicable fd-audit for resource-owning changes.
- R.4 Test daily/minute/mixed data selection and real native archive write/readback through the connected broker. Publish support evidence by broker/product, with controlled checks distinguished from real broker checks.
- R.5 Desktop/mobile, keyboard/focus, reduced motion, browser Back, empty/sparse/failed states and user-readable errors; no diagnostic clutter on the healthy path.
- R.6 Version/locks/host hooks, built assets, artifact hashes, clean package inventory, release notes and installation/update guide agree. The user can complete the selected release journey from those instructions.

**Done when:** the exact proposed release passes its declared environment and journey matrix. Unverified Docker/broker/OS combinations are not promoted to supported on the strength of a build or unit test. Publication is a separate release action once the concrete artifact and evidence are ready.

## 5. Every journey has an owner and an acceptance result

All IDs refer to the target catalogue. Current labels below describe relevant components, not full end-to-end completion.

| ID / journey | Current foundation | Delivery package | Acceptance result |
| --- | --- | --- | --- |
| J01 Capture idea | P | M1 | Reopen unfinished named idea with saved draft |
| J02 Existing signals | C/H core | M0/M2 | Daily and timed CSV loop works with native data |
| J03 Generate signals | P | M5.1 | Causal template → same saved research loop |
| J04 Import outside work | Partial source/evidence infrastructure | M5.3 | Versioned import reproduces or is clearly review-only |
| J05 Resolve input ambiguity | C/H bounded | M2.2/M6.1 | One relevant choice, preserved mapping, return to field |
| J06 Prepare data | C/H bounded | M0/M2.2/M6 | Matching cache plus missing-only broker acquisition and readback |
| J07 Read baseline | C/H summary | M2.4 | Supported metrics/benchmark and useful investigation |
| J08 Diagnose loss | Partial tables | M2.4/M4b | Chart ↔ actual trade/price context without losing filters |
| J09 Adjust setup | Missing completed-result transition | M2.1 | Exact parent opens child draft and linked result |
| J10 Optimize settings | C/H search core | M2.1/M3 | Baseline-linked study with full request planning |
| J11 Optimize allocations | C/H joint core | M3/M4b.3 | Feasible allocations compared on shared-account results |
| J12 Monitor/pause | C/H progress/recovery; pause P | M3.3 | Honest state, safe pause and preserved completed work |
| J13 Explore parameters | Trial table; rich plots P | M3.2/M5.2 | Actual trials support contextual native plots |
| J14 Manual hypothesis | P | M3.4 | Validated settings become recorded manual trial |
| J15 Extend study | Recovery exists; budget is identity-bound | M3.4 | Compatible extra-budget segment preserves history |
| J16 Refine search | P transition | M3.4 | Changed search is a linked child with preserved parent |
| J17 Shortlist | P | M1/M3.5 | Exact candidate retained across visits/studies |
| J18 Compare | P in joint UI | M4a.1 | Baseline/candidate comparison exposes material mismatch |
| J19 Reserved evaluation | C/H fixed split; history integration gap | M2.3/M4a.2 | Frozen candidate evaluated with truthful exposure history |
| J20 Walk forward | P in joint flow | M4b.1 | Causal training/evaluation windows with recorded reset/carry |
| J21 Stress assumptions | P in joint flow | M4b.2 | Bounded scenarios retain recipe and degradation results |
| J22 Transfer markets/periods | P transition | M5.4/M6 | New context re-resolves data/contract and preserves differences |
| J23 Engine discrepancy | C/H two bounded engines; reconciliation UI P | M4b.5 | First differing event/policy explained with exact evidence |
| J24 Combine strategies | C/H cash core | M1/M4b.3 | Saved components compose and compare without cash double-counting |
| J25 Exposure/capacity | Partial positions/contributions | M4b.3/M6 | Actual exposures; unsupported capacity estimates stay unavailable |
| J26 Recover work | C/H bounded | M0/M3.3/R | Resume after failure/restart without duplicate accepted evidence |
| J27 Return later | C/H saved runs; richer library P | M1 | Restore server draft, selected study and view context |
| J28 Retain/reject | P | M4a.3 | Append decision with chosen version and reason |
| J29 Replay/refresh | C/H replay; refresh P | M2.5/M7.1/R | Exact replay stays exact; newer data is a child result |
| J30 Export/share | C/H exact export | M4a.4/M5.3/R | Readable report or declared reproducible bundle with dependencies |
| J31 Execution observations | P connector | M7.3/M7.4 | Observed fills linked separately; no implicit activation |
| J32 Organize/retire | P library behavior | M1.5/M7.5 | Archive/restore, active-work policy and dependency-aware removal |
| J33 Upgrade/change broker | C/H bounded version checks | M6.5/M7.1/R | Capability recheck, compatible replay, old reports preserved |
| J34 Tabs/devices | C retry/ownership; browser draft only | M1.2/R | Revision conflict keeps both drafts; no duplicate launch |

A journey closes only after its accepted paths and applicable failure/return paths have current evidence. Asset-dependent journeys close separately for each admitted profile.

## 6. Asset rollout and automatic requirements

**Common admission sequence:** dated profile → authoritative historical streams → supported engine/account → contextual setup/results → complete research loop → saved replay/upgrade → broker/environment evidence.

Choose this initial order because it builds on current signal/candle semantics while isolating new account models. It is an engineering sequence, not a claim that broker data is available or a recommendation to trade those products.

| Wave / profile | User decisions | Automatic/native prerequisites | Admission proof / dependency |
| --- | --- | --- | --- |
| A0 Current NSE long cash, daily/intraday | Signals, allocations, capital, timing/holding, costs | Existing D/1m planner, native sessions and coverage | M0–M4a/R cash-equity loop; preserve existing financial policies |
| A1 Broader cash equities/ETFs/listed funds | Universe, direction, distribution/adjustment intent | Dated instruments, historical universe, corporate events, venue calendars/currency | M6 profile/history foundation; splits/dividends/delisting and source provenance reconcile |
| A2 Dated financial futures | Contract/roll rule, lots, collateral and position policy | Expired contracts, multiplier/ticks, expiry/settlement, margin and roll overlap | Start with an exact-expiry cash-settled financial/index contract; supported native account and real contract fills/MTM precede roll templates |
| A3 Equity/index options and multi-leg | Structure, expiry/DTE, strike/delta, exits/roll/hedge/assignment policy | Historical chains/legs, quotes or declared bar model, exercise/settlement, deliverables, margin | A2 account/instrument work where reusable; independently test legs, exercise/assignment and expiry |
| A4 Currency and commodity derivatives | Exact product, expiry/roll, account currency, delivery policy | Quotation/conversion, settlement, non-equity calendars, tender/delivery rules | Reuse A2/A3 only where economics match; each venue/product gets its own complete example |
| A5 Equity shorts/long-short | Intraday versus borrowed holding, exposure and borrow assumptions | Product eligibility, dated borrowing/financing, recalls, settlement/fees | A1 plus short-account engine contract; no zero-borrow overnight fallback |
| A6 Crypto spot | Venue/pair, sizing, reporting currency, cash/margin distinction | Venue instruments, quantities/minimums, fee currencies, uninterrupted calendar history | Independent of derivatives once M6 is ready; base/quote/fee balances reconcile |
| A7 Crypto perpetuals/dated/options | Linear/inverse contract, collateral/leverage, funding/roll/legs | Mark/index, funding, liquidation rules, settlement/chain streams | A6 plus corresponding derivative/account proof; inverse and linear examples separate |
| A8 Spot FX/CFDs | Actual broker product, units, execution/financing assumptions | Bid/ask or declared model, FX conversions, rollover/financing, stop-out | M6 currency/account contract; broker-specific product/history and financing test |
| A9 Direct bonds/bills | Exact security, face value, cashflow/reinvestment policy | Coupon/maturity/day-count, clean/dirty prices, accrued interest, defaults/calls if modelled | Separate engine-capability probe; native cashflow/valuation support required before UI commitment |
| A10 NAV funds/SIP | Scheme/plan/option, contribution/redemption dates and amount | NAV history, cutoffs/availability, dated charges, contribution cashflows | Separate valuation/contribution adapter; capital flows and money/time-weighted results reconcile |
| A11 Other/synthetic/OTC/physical/on-chain | Exact exposure and intended use | Domain-specific pricing, valuation/account and event streams | Classified and retained in scope inventory; each needs a named supported connector before executable admission |

Indices or continuous synthetic series may be valid signal inputs without being executable instruments. Cross-asset portfolios require compatible clocks, FX, collateral/margin and netting; individual profile acceptance does not automatically establish combined-account support.

The broad waves contain separately tracked admissions. A1 separates domestic cash enrichment, foreign-currency cash and listed income securities. A3 starts with a European cash-settled long option, then admits dynamic contract selection, short/multi-leg/hedged structures, physically settled stock options and American early-exercise/assignment independently. A4 separates currency futures/options from commodity futures/options, including option exercise into a future where applicable. A5 separates intraday shorting from overnight borrowed positions. A7 separates linear from inverse/quanto accounts and dated contracts from perpetual funding and options. A8 separates FX from broker-specific CFDs. A9 starts with plain fixed-coupon bonds/bills before floating, callable or default-event products. A10 separates simple contributions from redemptions, distributions and rebalancing. Each uses the detailed contract in ASSET_PROFILES and gets its own data, accounting, UI and real-source receipt.

This prevents an accepted simple subtype from advertising an entire family. Independent branches may advance when their feasibility is demonstrated; the wave numbers are the default focus order, not artificial dependencies on unrelated products.

A9–A11 are explicit discovery/admission packages, not promises that VectorBT or Nautilus can faithfully implement every product. If the selected engines cannot express the required semantics, the outcome is a documented unsupported capability or a named established-engine connector proposal, not a replacement custom financial engine.

**Broker matrix:** record broker, profile/venue, dates, interval/stream, expired-instrument support, timezone normalization, request bounds, authentication recovery, successful empty response handling, native persistence and exact reuse. Retain separate columns for implementation, controlled tests and actual broker checks. A second connected broker account is an external verification dependency; do not ask for passwords or simulate that evidence with a fixture.

## 7. What lands on each page, and when

The detailed fields stay in PRODUCT_JOURNEY_MAP and ASSET_PROFILES; this table assigns delivery ownership rather than creating another competing UI specification.

| Surface | First delivery | Later additions | Navigation/save contract |
| --- | --- | --- | --- |
| P0 Library | M1 experiment/study/setup/source/shortlist/archive views | M5 imports; M7 review filters | Preserve search/filter/scroll and reopen exact records |
| P1 Experiment | M1 question, notes, drafts, versions and existing results | M3 studies; M4 decisions; M7 reviews | Continue last context; children retain parent identity |
| P2 Setup | M1 server draft; M2 complete saved-result actions and reserved periods | M5 templates/search types; M6 asset fields | Autosave; run freezes version; back retains edits |
| P3 Data review drawer | M2 full request/coverage and specific issues | M6 richer streams/contracts | Healthy automatic path; return to relevant setup field |
| P4 Activity drawer | M2 existing progress in context | M3 truthful pause/continue; M7 scheduled work | Closing drawer leaves job active; actions match state |
| P5 Backtest | M2 focused performance/trade/settings views and action links | M4 deeper comparison/diagnosis; M6 product views | Chart ↔ trade and Back to study preserve context |
| P6 Study | M3 overview/parameters/trials/shortlist | M5 conditional/multi-objective/advanced views | View choices do not change scientific identity |
| P7 Compare | M4a baseline/candidates | M4b portfolio/counterfactual/engine reconciliation | Retain selection; linked source and return location |
| P8 Validate | M4a reserved candidate evaluation | M4b rolling/scenarios; M6 product checks | Frozen plan/report; revision creates a child |
| P9 Portfolio setup mode | M1/M2 reuse existing joint setup | M4b saved components; M6 supported account models | Keep basket when inspecting/editing a component |
| P10 Decision/handoff | M4a retain/reject/revisit and export | M7 supported observation/handoff adapters | Decision history append-only; activation separate |
| P11 Review over time | M7 explicit refresh and version differences | Opt-in schedule/observations for supported connectors | New evidence; older conclusions remain readable |

Maintain one Research navigation entry. These are focused views and drawers inside it, not twelve competing sidebar products.

## 8. Migration, compatibility and acceptance details

### Preserve the existing user's work

1. Inventory schema and artifact versions, links and dependencies; compare archive/report hashes before and after migration.
2. Add new metadata with idempotent migrations. Attach old jobs to inferred single-run containers only where relationships are proven; otherwise leave them in accessible legacy reports. Do not invent a baseline, trial timing, validation status or user decision.
3. Retain old job URLs, exports and compatible exact replay readers. An unavailable older runtime still permits report review; it does not authorize recalculation under new rules.
4. Migrate new-study metadata separately from immutable numerical evidence. Freeze new policy/contract versions when semantics intentionally change and test the difference.
5. Backup/restore must include native stores, research metadata/artifacts and any managed study store. Interrupted migration, repeat migration and rollback to matching source/runtime/data get their own acceptance.
6. Audit database sessions, figure generation, worker/subprocess lifecycle, sockets and files when their owning code changes. No new session or process may escape cleanup.

### Evidence required to close a package

| Test layer | What it proves | What it cannot replace |
| --- | --- | --- |
| Hand-checkable engine cases | Quantities, timing, cash/fees/margin/cashflows, attribution and intended engine differences | Broker data availability or UI usability |
| Controlled service integration | Native store behavior, ownership, retries, missingness, full-range planning and job boundaries | A real broker response |
| Browser journey | Actual controls, transitions, save/back/pause, sparse/failed states, keyboard/mobile behavior | Correct financial calculations without matching evidence |
| Real broker path | Required data from that named broker, native write/readback/reuse and recovery | Every broker/product/history range |
| Release lifecycle | Fresh install, populated upgrade, restore/restart and compatibility of the exact bundle | Unaided product comprehension |
| Unaided user exercise | A person completes the declared loop without developer instructions or parameter copying | All numerical edge cases or unsupported markets |

Record the source revision, release artifact hash when applicable, environment, accepted profile, input/snapshot identity, expected versus actual result and evidence location. Keep private data and machine receipts out of Git; publish only safe summaries and controlled examples.

The current historical check counts remain in STATUS/VERIFICATION. Do not add them together as a new acceptance total, and do not call every proposed journey “tested” because its underlying engine suite passed.

## 9. Execution order and completion reporting

Start with these concrete work items:

1. M0 capture and classify the current daily/timed/optimization/replay/recovery paths.
2. Fix critical baseline defects revealed by those observations.
3. M1.1 introduce the product-container/relationship migration and preserve old links.
4. M1.2 implement server drafts/version conflicts/idempotency with persistence acceptance.
5. M1.3–M1.5 deliver the Library and Experiment experience, including reopen/archive.
6. Prioritize M2.6 automatic download continuation and truthful animated run progress from native user acceptance; then finish M2.1–M2.3 result actions, full request inference and reserved-period history.
7. M2.4–M2.5 finish backtest investigation and replay/refresh distinctions.
8. M3.1 prove durable Study/Trial mapping and compatible recovery, then M3.2–M3.5 expose exploration, pause, extension and shortlist.
9. M4a complete comparison, candidate validation and decisions; finish R for the first complete cash-equity release.
10. Continue M4b/M5 and M6 admission packages, then M7; every shipped increment passes its applicable R checks.

Independent early work may include M6 historical-data probes and R install/upgrade fixtures. Do not parallelize schema/identity changes across competing implementations; land one reviewed contract and then build dependent views.

For each package, maintain one state: **not started → implementing → component checked → journey observed → accepted**; use **blocked** only with a specific dependency and the work that can still proceed. For each journey/profile, retain its current evidence level and gap. Report completed user outcomes, the next demonstrable outcome and material unresolved dependencies. Avoid an aggregate “percent complete” across incomparable assets and workflows.

**Planning-task result:** this document is the integrated delivery plan. Source findings have been checked and historical evidence referenced; the new planned app journeys have not been executed or implemented by writing it.
