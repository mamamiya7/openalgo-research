# Automatic research: philosophy and production priorities

Reviewed 24 October 2026 against the decision-policy batch in the working source.
This is a product and research-method review, not a claim that installation,
the installed browser journey, or future financial performance has passed.
No private dataset, account, scanner identity or financial result is reproduced.

## What the user is asking the product to own

Given the available CSV or Chartink signals and broker prices, the application
should decide what can sensibly be researched, choose permitted parameters and
sizing, diagnose inadequate capital or unusable settings, and finish with an
understandable answer. The user should not have to interpret trial logs or
manually discover basic concentration, sample or funding problems.

The intent is useful decisions from the data available, rather than a profitable
answer at any cost. Preserve hard risk limits, causal prices, costs, whole-share
execution and honest validation. Choose date boundaries from information
availability and complete holding periods before outcomes. Do not choose a
profitable calendar interval after seeing results. If the evidence cannot support
a useful setup, that is a valid completed answer.

The production contract also includes bounded work, reproducible saved evidence,
and a responsive journey. Faster execution must not reduce the declared search
or remove financial checks. Small regression tests qualify corrections; repeated
large user-data experiments should answer a specific unresolved question.

## How the current batch measures up

| Responsibility | Current implementation | Assessment |
| --- | --- | --- |
| Work with the available data | Outcome-free complete-entry calendars, history-aware periods and explicit historical scope | Substantially aligned. Overlapping fits cannot supply independent evidence, and a long maximum holding range still removes recent entry opportunities. |
| Learn from failed proposals | Optuna receives named support, sample-design and native-constraint feedback | Aligned within the registered catalogue. This is adaptive search, not unrestricted reasoning about new strategy families. |
| Set an economic goal | Positive net return, improvement over the original account, risk-adjusted objective, cost and neighboring-setting challenges | Stronger than selecting the largest historical score. A passing historical candidate remains distinct from independent validation. |
| Allocate sensibly | Original sizing, reduced sizing, risk-target choices and source-universe cluster allocation, with hard account limits | Partial. A legal order size is not necessarily an appropriate portfolio target. Weights reserved for inactive universe members can leave eligible opportunities underfunded. |
| Explain required capital | Fixed-initial-ticket planning variant, native verification, cash versus noncash diagnosis | Partial. The estimate is conservative and is neither minimum capital nor an optimum capital allocation. It does not jointly select the strategy and its capital. |
| Stop intelligently | Frozen budget, original-setting fallback, explicit verdicts and no more capital increases after noncash blockers | Safe and bounded. Diagnosis often ends with a review action instead of resolving the next permissible research choice automatically. |
| Save and resume | Frozen recipes, account identities, durable attempts and retained evidence | Essential foundation. Installed Chartink-to-save-to-reopen acceptance is a separate integration check. |

Relevant implementation owners are [automatic decisions](../../research/automatic_decision.py),
[automatic admission](../../research/automatic_design.py),
[available-history planning](../../research/available_history_protocol.py),
[execution choices](../../research/execution_search.py),
[allocation](../../research/portfolio_allocation.py) and
[capital diagnosis](../../research/capital_planning.py). The existing
[batch research note](AUTOMATIC_DECISION_RESEARCH_20261024.md) records its narrower
implementation and qualification scope.

## Fresh primary-source comparison

These projects solve different layers. Their existence does not establish an
adoption ranking or justify replacing the native account engine.

| Primary source, checked 24 October 2026 | Relevant practice | Application here |
| --- | --- | --- |
| [Freqtrade Hyperopt](https://www.freqtrade.io/en/stable/hyperopt/) | Explicit search spaces, objectives, minimum-trade settings, seeds and bounded epochs; the resulting setup is backtested again with matching assumptions. | Keep objective and evidence requirements explicit. Optimizer completion and a positive score are insufficient product conclusions. |
| [Freqtrade lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/) | Perturbed backtests compare indicator and entry/exit behavior to expose future-information dependence. Its diagnostic deliberately alters wallet and trade-limit assumptions. | Add invariance checks where useful, but never present an unconstrained diagnostic account as the user's executable financial result. |
| [Optuna 5.0 Trial API](https://optuna.readthedocs.io/en/v5.0.0/reference/generated/optuna.trial.Trial.html) | Named constraints participate in feasibility; all constraint values must be non-positive for a feasible trial. | The pinned sampler can learn application-defined feasibility. It cannot decide whether those financial constraints or the available evidence are appropriate. |
| [RD-Agent(Q) research paper](https://arxiv.org/abs/2505.15155) and [Microsoft repository](https://github.com/microsoft/RD-Agent) | A research stage proposes hypotheses, development implements experiments, and experimental feedback guides later directions. | Adopt explicit feedback-to-next-action transitions. Its factor/model research and generated-code machinery do not demonstrate correctness for imported scanner signals or justify unbounded experiments. Published benchmark results are not evidence of performance here. |
| [Qlib task management](https://qlib.readthedocs.io/en/stable/advanced/task_management.html) and [workflow](https://qlib.readthedocs.io/en/stable/component/workflow.html) | Task templates generate experiments over declared periods; execution records loading, processing, model work and evaluation artifacts. | Record why each permitted period or follow-up task exists. Preserve datasets, assumptions and outcomes together so decisions can be reconstructed. |
| [QuantConnect walk-forward optimization](https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization) | Training windows and retraining frequency are explicit design choices with adaptation and overfitting trade-offs. | Automatic period selection needs a predeclared rule. Repeated fitting on observed history is not automatically independent confirmation. |
| [QuantConnect portfolio construction](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/portfolio-construction/key-concepts) and [risk management](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/risk-management/key-concepts) | Active insights produce portfolio targets, which pass through a separate risk layer before execution. | Separate desired allocation among actual opportunities from hard financial caps. Capping source-universe weights alone does not answer how much to buy now. |
| [Bailey, Borwein, Lopez de Prado and Zhu: probability of backtest overfitting](https://escholarship.org/uc/item/4w1110bb) | Selecting among historical strategies creates a statistical problem beyond testing one fixed strategy; the paper develops a framework to assess it. | Preserve search provenance and acknowledge selection bias. Do not claim that this application computes that paper's probability, or that ordinary historical challenges remove selection effects. |

## What remains incomplete

**Selection, allocation and capital are coupled economically.** Current search
can choose sizing modes, but the capital estimate is calculated after freezing
the winner and preserves an original initial rupee ticket. It is a different
planning account. A displayed estimate must therefore never imply that this is
the capital required to reproduce the selected percentage-sized report.

**Low investment needs a cause, not a reward or automatic penalty.** Idle cash
may reflect absent opportunities, position limits, conservative stops, liquidity,
whole-share affordability, or weights reserved for inactive stocks. Spending all
cash can worsen results. The useful next step is a causal explanation followed
by a permitted allocation experiment, not forcing utilization upward.

**Stopping an inappropriate capital increase is not the whole autonomous loop.**
The new noncash diagnosis prevents a false funding recommendation. It does not
yet automatically choose a valid alternative ticket, allocation policy or
opportunity schedule. Percentage-based limits can sometimes admit additional
fixed-ticket orders at higher account capital; that is a policy/account
comparison, not proof that the only problem was cash.

**Rejection must not become a hidden search for another profitable winner.**
Costs and neighbors challenge the frozen choice. A failed challenge cannot
silently promote the next candidate. Any future shortlist or diagnostic repair
programme must be declared before outcomes, charged to its budget, and treated
as additional searched evidence. Known history cannot become untouched again.

**Available history sets an evidence ceiling.** Captured positive signals do not
reconstruct absent scanner rules or non-triggering examples. Reused periods,
seeds and neighboring configurations add diagnostics, not new observations.
The product should finish helpfully within that ceiling without promising that
every file can yield an independently supported strategy.

## Next production priorities and acceptance

1. **Explain the complete opportunity funnel.** Separate rule-filtered and
   incomplete-horizon signals from unavailable prices, cash clipping, risk caps,
   position limits and allocation weights. Acceptance: overlapping constraint
   tags remain visible; counts reconcile to the exact native ledger; no single
   tag is asserted to be the unique cause without additional evidence.
2. **Add a bounded diagnosis-to-action policy.** Define permitted responses for
   each failure before results: reuse native missing-price preparation, diagnose
   whole-share affordability, compare allowed sizing choices, or conclude that
   the frozen limits prevent the goal. Acceptance: no hard limit is relaxed,
   no unsupported signal is invented, and every transition has a reason,
   remaining budget and reproducible stop condition.
3. **Qualify opportunity-based allocation as a new financial contract.** Allocate
   among contemporaneously eligible and held positions using prior information,
   while retaining shared cash, costs, capacity, correlation and risk limits.
   Acceptance: adding an inactive source-universe member does not arbitrarily
   reduce all active tickets; same-opening competing entries remain deterministic;
   duplicate symbols, whole shares and every hard cap reconcile natively.
4. **Make period intelligence explicit.** Explain complete-entry dates and any
   censored tail. If introducing horizon tiers, freeze them before outcomes and
   compare matched cohorts within each tier. Acceptance: changing future profits
   cannot change planned windows; smaller-horizon results cannot gain an
   undisclosed advantage from different data; historical scope remains clear.
5. **Define the capital question precisely.** Distinguish a conservative planning
   estimate, a tested sufficient amount for a specified opportunity policy, and
   reproduction of the selected account. Acceptance: zero-amount or noncash
   failures never produce a verified capital claim; every tested amount preserves
   its declared ticket and limits; the original winner remains unchanged.
6. **Keep promotion and performance claims evidence-based.** Acceptance: weak
   historical profit can fail costs; low-support or concentrated results are
   rejected automatically; failure never extends the search until profitable;
   restored and standalone saved reports retain the same verdict. Speed targets
   are measured on the complete declared journey, separately from small tests.

The current batch is a credible bounded decision layer. Full alignment with the
user's philosophy additionally requires automatic, constrained diagnosis and
portfolio construction. More backtesting engines or an LLM label alone do not
close those product gaps.
