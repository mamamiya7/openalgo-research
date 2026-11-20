# Integrated scan research and AI control

Implementation plan, 15 November 2026. The hands-off fixed-signal slice is now
implemented and extended with a unified reviewed flow. Replay and improvement,
hands-off/guided/hands-on controls, and per-field adjustment permissions are
implemented. Subset selection and automatic routing to optional engines remain
proposed. Hands-on selects exact replay; guided controls each new AI experiment.

## Unified user flow

One review screen now chooses exact replay or improvement of unchanged scan
signals. Hands-on runs the reviewed settings once without AI, optimization or
automatic capital changes, and saves an unqualified backtest. Guided improvement
persists each executable proposal, pauses the native worker safely, and resumes
the same checkpoint only after approval. Later recovery experiments use the same
approval boundary. A user stop saves its actual reason without claiming failed
validation. Hands-off continues automatically within the reviewed authority.

Capital, ticket size, holding, stop, target, trailing and allocation can be fixed
or adjustable. Locks cover related ATR/sizing semantics and native recovery,
not just model output. Required cost, nearby-setting and historical checks still
run unchanged; a diagnostic perturbation is not a selected setting. Complete
configurations and proposed domains are visible before a guided approval.
Reviewed plan hashes, development evidence and checkpoint identities bind the
same authority. Repeated approvals do not create another model request or job.
Small frozen-data native qualification and local installation are recorded in
`STATUS.md` and ignored `.agent-native/unified-flow/` receipts.

## Implemented fixed-signal slice

New reviewed journeys opt into `hands-off-fixed-signals-v1`; existing saved runs
without that marker retain their previous programme. The reviewed Start pins
the configured model, all imported source identities, one trading style, costs,
hard risk limits and the existing 408-proposal/512-account allowance.

Mandatory native screening remains intact. At two refinement boundaries in each
of the six existing fits, the configured AI receives only that fit's completed
training evidence: complete configurations, admission failures, exit outcomes,
occupancy, observed holding durations and earlier plan feedback. Frozen source
coverage counts distinguish a small training window from the complete source;
later/final outcomes are not planning inputs. The compiler admits supported
numeric holding/exit/sizing ranges within the already-prepared native domains.
Optuna proposes configurations and VectorBT performs the financial calculations.
Nonplanned controls remain anchored to an actual screened configuration.

The AI can explicitly search, defer to the reviewed native domains for that
block, or stop the whole study with a reason. Proposed ranges are never labelled
validated. Up to twelve initial planning calls are durably debited before the
provider request; interrupted calls are not blindly retried. The combined model
allowance is 24, with the existing later strict-phase cap of three retained.
Planning does not multiply the financial search allowance. Native capital
recovery, acceptance checks, selection and exact saving/reopening retain their
authority. Invalid or unavailable model output preserves progress and stops
with attention required rather than silently substituting a controller plan.

Qualification uses small frozen saved-data cases, including a real five-stock,
68-signal cohort and an explicitly synthetic three-stock, 90-signal control.
Private native-worker receipts and model traces are retained under ignored
`.agent-native/hands-off-flow/`; no full scanner or new broker download is used.
The final qualification outcome is recorded in `STATUS.md`.

## Outcome and starting point

Start from an existing Chartink/CSV selection. Preserve its observations, source
rows and dates. Let a user choose what may change and how involved they want to
be; give the configured AI a bounded, executable research programme to pursue.
The outcome is the strongest supported configuration that passes all applicable
checks, or an honest saved finding that no tested setup passed. It is never a
promise of profitability, a global optimum or an obligation to spend every trial.

Use native React/Flask/SQLAlchemy jobs, Historify, the existing configured-model
connection, Optuna and the engine adapters. No new general backtester, optimizer,
agent server or arbitrary generated strategy-code execution is needed.

## Current evidence and the quick-test gate

- Existing reviewed v16 plans bind `fixed-signals-v1`, one trading style, fixed
  hard risk limits and VectorBT. The playbook explicitly rejects added filters.
- The controller offers registered complete experiment configurations. The model
  can select a candidate or stop; it cannot freely create methods or engine calls.
  Native funding recovery is distinct from an actual provider decision.
- Same-study configuration patches, development scorecards and measured feedback
  already exist. They are reasoning context, not model training or new evidence.
- OpenAlgo Rust SDK 2.2.0 is optional and isolated from main SDK 2.0.5. Its adapter
  currently rejects automatic research and overlapping repeated signals; native
  charges and opening holding exits differ from VectorBT. Nautilus is an optional
  bounded Linux/WSL execution adapter, not the reviewed AI programme's default.
- Prior small real saved-data comparisons prove engine execution and exact
  save/reopen. They do not establish a fresh model-to-engine feedback loop.
- Fresh configured-model capability probe: **passed within the scope below**.
  Private receipt: `.agent-native/ai-loop-proof/completed.json`.

Actual `chatgpt/gpt-6.1-sol` made two provider decisions around two actual VectorBT
accounts on two saved signals/five sessions. A 100000-capital baseline with 1%
tickets could not fund either share. The model chose the offered 674000-capital
repair, preserving tickets, exits, signals and 10 bps costs; both signals closed.
The unchanged native `automatic_design.assess` returned unqualified:
two closed trades below 30, two entry dates below 10,
insufficient calendar coverage and one-stock concentration of positive profit.
The actual results, gate failures and complete configurations fed the next model
decision. It stopped: holding 2/3 cannot repair sample/date/diversity under fixed signals/no new data.
No controller fallback or response, engine-result or gate mocks were used. Mandatory
admission failed, so full validation did not run; no acceptance or market edge is claimed.
The exact trace/reports were saved and reopened with model/engine calls forbidden:
zero new accounts. Probe limits were eight accounts/three calls; actual usage two/two.
Production retains 408/512. A tiny custom catalogue/dispatcher used the native model
selector; full durable worker/compiler acceptance remains open. Optuna was offered
but not selected. AI control of Optuna, OpenAlgo Rust and Nautilus remains untested.
OAuth stayed in place, metadata was read-only; only credential-mirror persistence
was disabled. No source DB copies, broker calls, new prices or scanner runs occurred; production funding remains native.

## User contract: two independent choices

| Research intent | May change | Must preserve |
|---|---|---|
| Replay the scan | Missing execution assumptions proposed before review only | All original signals and the reviewed fixed configuration |
| Improve how the scan is traded | Permitted exits, holding, capital, tickets and registered allocation methods | Original signal selection, style and locked settings |
| Refine opportunities within the scan | Registered causal filters and optional trading research | Original candidate pool and actual historical signal times |

| Involvement | Execution authority |
|---|---|
| Hands-off | One reviewed bounded programme; explain and continue automatically |
| Guided | Persist the proposed next experiment; await approval before executing |
| Hands-on | Run the exact user-selected configuration; suggestions do not execute |

One study screen supports these controls without separate user accounts or
duplicate product flows. Hands-on selects exact replay; guided is the stepwise
way to control AI improvement. Remember choices but review material changes.
Show source/history/exclusions, trading style, capital/sizing policy and risk
boundaries together. Each adjustable setting has `AI may adjust` or `Keep fixed`.
“Trade all” reports actual admitted/filled/unfunded/excluded counts; limited cash
must not silently convert it into AI-selected subset research.

## Versioned research mandate

Add a new explicit mandate and recipe version; never reinterpret saved v16 runs.
Freeze before outcomes: intent, involvement, objective, source/cohort identities,
baseline settings, per-field permissions, allowed methods and parameter domains,
style, risk constraints, capital policy, date/period roles, supported engine,
execution/fee/settlement assumptions, data/features and validation programme.
Retain 408 proposals and the 512-account ceiling for automatic research. Account
for broad screening, refinement, funding, retries, checks and exact reconstruction
within that single programme; never grant a new allowance for each AI iteration.
Model calls retain a separate bounded reservation; replay is one fixed test, not the full research programme.

The compiler must publish integer stage allocations and reservations before Start:
feasibility accounts; broad-screen and refinement proposal slots across all
families/fits/seeds; mandatory validation accounts; exact reconstruction; and
uncertain/failed attempt debits. Screen plus refinement retain all 408 planned
proposal slots. Their maximum native account cost plus every required reserve
must fit within 512. Reserve a full experiment and its checks atomically before
execution; unused reservations are released only after reconciling actual work.
There is no new 408 grant per iteration, engine or family. Reject an infeasible
programme before running; never truncate required checks or the proposal programme
to fit a newly introduced filter-by-setting cross-product.

Default capital policy estimates required starting funds without a preset user
ceiling. A fixed-capital choice instead constrains whole-share tickets to its
available cash. Both retain allocation and hard risk limits. More capital cannot
repair concentration, percentage risk caps, missing evidence or a weak strategy.
Include costs and simultaneous holdings in funding; compare return/risk after
costs so a larger rupee profit from larger funding cannot become the objective.

## Executable AI loop

1. Prepare the full permitted data/feature union once, from Historify first.
   Retain fixed coverage exclusions and acquire no prices per trial. Use only
   historical observations actually supplied; today's stock list is not evidence
   of what a scanner would have selected on earlier dates.
2. Run the reviewed baseline through the chosen engine; retain complete settings,
   fills, failed orders, costs, occupancy, capital usage and baseline validation.
3. Give AI development evidence plus its mandate, supported actions, remaining
   budgets, previous hypotheses and complete tested configurations. Missing
   measurements remain unknown. Do not expose sealed final-period performance.
4. AI chooses a registered research family and states problem, mechanism, expected
   evidence and rejection/stop conditions. Broad choices concern an economic
   mechanism; adjacent numbers alone are not a research hypothesis.
5. Compile its proposed experiment against the mandate. Refuse locked-field,
   unsupported-data, unsupported-engine or out-of-budget changes before execution.
   A compiled experiment carries a complete configuration identity, domain,
   baseline/comparator, causal inputs, budget reservation and validation binding.
6. Let Optuna search admitted numerical domains: holding, supported exits, ticket
   size and allocation. Treat coupled settings together. Screen meaningful
   alternatives, then refine supported promising regions within the frozen
   programme; do not bypass all-family requirements or shrink the search budget.
7. Normalize native outputs into measured feedback and unchanged validation
   scorecards. Separate proposed settings, actual applied settings, simulated
   execution, unchanged fills, risk-state changes and unknown effects.
8. AI compares expected and observed evidence, then continues, changes hypothesis
   family or stops. Reject complete duplicates; use no-effect evidence to avoid
   equivalent tests, while retaining distinctions relevant to risk validation.
9. Validate the exact final candidate using the existing checks and period-use
   reservations. Failed development checks can inform development; a consumed
   final holdout cannot become fresh independent confirmation after retuning.
10. Automatically select only a qualifying candidate; otherwise save the baseline,
    rejected alternatives and reasoned stop. Reopen/export without recalculation.

An experiment must name `mandate_id`, `baseline_id`, supported method, complete
candidate/domain, reason, expected measurements, input requirements and stop rule.
AI supplies reasoning and selects allowed actions. The server validates and
reserves; Optuna searches; engines calculate; native validation owns acceptance.
No model response can edit a threshold, declare itself accepted or run live orders.

## Engine policy

Keep VectorBT as the first automatic implementation and preserve independent
signal lots. Publish a capability contract for timing, repeated lots, features,
costs, sizing, risk, validation and runtime availability. Pin one compatible
engine/policy to a study. Explain incompatibility rather than removing settings.
Allow OpenAlgo Rust or Nautilus automatic routing only after the same intent and
validation contract is qualified for a declared subset. Cross-engine verification
is explicit, uses compatible assumptions and its own accounted checks. An AI
must not change the engine because another fill policy produces higher PnL.

## Subset selection contract

Start with already eligible imported observations at each historical decision time.
Admit only registered causal predicates/ranking recipes with available pre-entry
features. Optional maximum picks applies per decision time, not retrospectively
to the whole backtest. Rank/tie rules are frozen before outcomes: stable source
strategy/CSV order resolves equal scores, with deterministic symbol identity as
the final tie-breaker. No outside symbols or future performance enter selection.
Retain every original observation and baseline; save selected/rejected decisions,
feature timestamps, recipe/threshold/rank, tie outcome and reason for each source
row. Distinguish filter rejections from missing evidence, execution refusal and
unfunded trades. Selection and trading parameter families share the same admitted
programme and full checks; a large cross-product cannot create a hidden new budget.

## Delivery sequence and integration points

| Batch stage | Existing areas to extend | Completion condition |
|---|---|---|
| 0. Bounded capability proof — passed | Native configured decision adapter and tiny saved-data harness | Two actual model decisions/two VectorBT accounts, admission rejection and reasoned stop; production acceptance remains open |
| 1. Mandate and permissions | `research_mandate`, `automatic_protocol`, `research_playbook`, journey/library metadata | Three intents and three involvement levels admitted explicitly; old identities unchanged |
| 2. Research compiler and loop | `research_experiments`, `research_supervisor`, `research_supervisor_program`, configuration context, scorecards and study memory | AI can request supported mechanism/domain experiments; complete settings and budgets remain authoritative |
| 3. Fixed-signal production path | Existing Optuna adapter, capital/operating validation, recommendation/exact-save code | Baseline and trading research complete with feedback, reasoned stops, exact save/reopen |
| 4. Subset research | Existing entry-condition/stock-feature registries and causal replay | New intent gates filters; original baseline retained; causal coverage and selection-bias checks pass |
| 5. Unified controls and findings | Portfolio builder, reviewed plan/progress, results/library APIs | Simple intent/involvement controls; locked settings honoured; clear decisions and exclusions |
| 6. Optional-engine qualification | Connector capabilities, runtime/portfolio adapters, execution-version pins | Only proven compatible requests can route automatically; unsupported cases remain blocked |
| 7. One delivery batch | Distribution checks, installer/restart, docs and cleanup | Exact qualified source/package; Windows/Linux checks and small end-to-end walkthrough |

Stages 1–5 form the integrated user flow. Stage 6 must not hold up the reliable
VectorBT path. Keep unfinished subset/engine intents visibly unavailable until
their acceptance gates pass; do not expose a selector that silently falls back.

## Small-case acceptance and operational checks

- Replay: all usable original signals retained; repeated entries stay independent;
  insufficient funds/data are disclosed and no optimization runs without authority.
- Fixed-signal AI: actual model chooses and executes a meaningful coupled change,
  receives measured feedback, chooses a second useful test or a justified stop;
  native funding decisions and controller interventions are labelled separately.
- Funding: high-price whole shares, overlapping holdings, nonbinding extra capital,
  fixed-capital clipping and percentage-cap failures; exact changed account checked.
- Subset: filtering uses only pre-entry data and imported candidates; retained and
  rejected opportunities have causal reasons. Compare original, trading-only and
  filtering effects fairly. Add multiple-testing/selection checks without removing
  existing gates; insufficient trade count never earns an accepted recommendation.
- Confirmation: unchanged chronology, costs, risk, neighbor, distribution and
  sample checks; sparse data yields a truthful exploratory/insufficient outcome.
- Permissions: locks survive AI proposals, guided approval, cancellation, restart
  and updates. Stale proposals cannot execute against replacement preparation.
- Model failure: expired auth requests sign-in; malformed output, timeout and
  uncertain completion preserve durable debits and do not create blind retries or
  undocumented fallback tests. Resume recovers exactly once after reconciling state.
- Persistence: model/experiment/configuration/engine/data identities and budgets
  survive saving, process restart and reopening; zero financial accounts on reopen.
- Resources: apply fd-audit to changed files/DB/process/model streams; bounded
  metadata, closed streams/sessions/logs, reaped workers and cancellation cleanup.
- Reuse checkouts, environments and immutable datasets. Keep only useful receipts;
  remove task scratch. Update the existing reviewed manual cleanup helper if
  deletion is blocked; report pending cleanup instead of claiming removal.

No full scanner run, weaker financial validation, live order, new broker downloader
or automatic model/provider fallback is part of this plan. Release scope must say
which intents and engines actually passed, and distinguish engineering controls
from independent evidence of a profitable market strategy.
