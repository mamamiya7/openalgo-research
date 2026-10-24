# Automatic research decision policy — 24 October 2026

## Product goal

Find a feasible improvement over the original account and holding cash that
survives predefined historical support, cost and local-parameter challenges.
Complete automatically with a supported candidate, insufficient evidence, or no
useful improvement. Never extend the search until a profitable result appears.
Never choose profitable validation dates or change the objective after results.

## Five established open-source engines evaluated

These are a practical shortlist, not a verified ranking of installed usage.
GitHub stars, repository activity and project documentation are adoption signals,
not measurements of production use. The projects solve different layers.

| Project | Relevant capability | Fit for this application | Decision |
| --- | --- | --- | --- |
| [Optuna](https://github.com/optuna/optuna) | Adaptive parameter optimization with named feasibility constraints | Already pinned to 5.0.0; ask/tell, deterministic seeds and exact recovery already integrated | Use its existing engine and documented `Trial.set_constraint` API; do not invent another optimizer |
| [Ray Tune](https://docs.ray.io/en/latest/tune/index.html) | Experiment orchestration, search integrations, repeated evaluations and recovery | Useful for distributed experiments; unnecessary migration for a bounded local worker | Retain bounded local orchestration; reconsider only when distributed execution is a requirement |
| [Freqtrade Hyperopt](https://www.freqtrade.io/en/stable/hyperopt/) | Trading-specific parameter search, explicit loss functions, minimum-trade rules and repeatable backtests | Useful research reference; crypto trading/exchange and strategy semantics differ from native NSE cash accounts | Adopt explicit objective and admission principles; retain native accounting and hard-risk semantics |
| [Microsoft RD-Agent](https://github.com/microsoft/RD-Agent), with [Qlib](https://github.com/microsoft/qlib) | LLM-based hypothesis, experiment and feedback loops for quantitative R&D | Most directly relevant agentic research pattern; unrestricted generated experiments need data and execution boundaries | Use the bounded hypothesis → experiment → feedback structure; no new remote model dependency or arbitrary generated execution in this batch |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence) | Agent workflow state, persistence, recovery and explicit transitions | Useful for complex mixed deterministic/LLM workflows; the current app already persists exact native study state | Keep the existing durable worker; use explicit decision stages and recorded outcomes rather than replace it for branding |

Primary references checked on 24 October 2026:

- [Optuna 5.0 trial constraints](https://optuna.readthedocs.io/en/v5.0.0/reference/generated/optuna.trial.Trial.html): feasibility is defined by all named constraint values being zero or negative. This matches the installed, pinned version; older `constraints_func` examples are not used.
- [Ray Tune search algorithms](https://docs.ray.io/en/latest/tune/api/suggestion.html): search integrations include Optuna, search state recovery, repeats and concurrency controls.
- [Freqtrade Hyperopt](https://www.freqtrade.io/en/stable/hyperopt/): optimization has explicit parameters, loss functions and result reproducibility. Replacing an accounting engine is not needed to use those design principles.
- [RD-Agent](https://github.com/microsoft/RD-Agent): proposes hypotheses, implements experiments and uses experimental feedback. Those patterns do not establish that an LLM can validate financial evidence.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence): checkpointers and stores provide different persistence scopes. Our existing append-only evidence and exact recovery already fulfill the narrower application requirement.

No external project code is copied. No extra LLM service is called, and no model
key, model cost, generated Python execution or new accounting dependency is
introduced. This batch uses the existing open-source optimizer's actual
constraint API and implements application-specific financial decisions around it.

## Evidence that motivated the policy

The preceding complete 146-stock/1,869-signal study proposed 408 configurations.
Its admitted winner earned ₹6.60 on ₹100,000. An exact native rerun reproduced
both its summary and ledger. Raising its fee setting from 10 to 12 basis points
changed net profit to −₹2.81; at 20 basis points it lost ₹40.45. All four
diagnostic accounts retained native financial validation. This demonstrates why
positive historical profit and completion of a budget are insufficient outcomes.

The original allocation is a separate financial contract. It apportions weights
across the source universe, and the winner deployed little cash. Restoring the
original allocation while keeping its rules increased use of cash but lost
₹841.74. Higher deployment is therefore a diagnostic, not an optimization goal.
This batch does not silently redefine portfolio allocation or force investment.

## Implemented automatic sequence

1. New editable v16 requests explicitly select `automatic_decision_v1` together
   with the existing design and available-history policies. Old requests and
   frozen studies retain their earlier calculations and replay identities.
2. Before outcomes, freeze the objective, goal, input scope, complete-entry
   calendar, available validation mode, decision stages and unchanged budget.
3. Complete the same six fits and all 408 proposals. Feed ordinary support,
   historical sample-design and native constraint failures to Optuna as three
   named constraints before telling each score. Final selection uses the same
   admission conditions; missing evidence cannot gain admission.
4. Freeze the primary choice. Never use a higher-scoring repeat seed or a
   challenge outcome to rerank it. If no changed candidate qualifies, retain
   the original settings and conclude no useful improvement.
5. For a changed exploratory winner, evaluate a higher-cost candidate/reference
   pair, up to four deterministic active-axis neighbors, and the fixed winner
   on three historical prefixes. The existing challenge planner fixes nearby
   settings before their results. Require at least two neighbors, at least 75%
   passing existing support/design/native checks, and at least half the original
   return improvement at the median neighbor. Costs must preserve a positive
   net return, score improvement and the existing sample/native checks.
6. Reserve those nine additional accounts before fitting. The worst-case
   exploratory programme including all 64 lost-attempt slots is 496, within
   the unchanged 512 ceiling. Chronological studies reuse their existing cost
   and neighbor accounts, so their existing ceiling is unchanged. Prefixes and
   seed repeats remain diagnostics, with zero independent confirmations.
7. Diagnose capital separately, without changing selection or reported returns.
   Increases are warranted only by recorded shared-cash shortfalls. Fixed risk,
   position, allocation, liquidity, ticket and missing-data blockers stop further
   increases and receive an explicit explanation. Multiple recorded constraint
   tags remain visible; they are not asserted to be unique binding causes.
8. Produce an explicit decision and next action. A fragile historical winner
   receives `no_useful_improvement`; a candidate surviving challenges without
   independent later evidence receives `insufficient_evidence`. Independent
   support requires the existing later-period validation to pass as well.

## Boundaries and remaining work

- A short positive-only scanner history cannot supply missing scanner rules,
  non-triggering stocks or independent future outcomes. No engine can infer
  that evidence legitimately. Historical challenges still run automatically.
- The existing chronological admission standard is retained. This batch does
  not weaken it by relabeling fewer folds as a fully validated recommendation.
- Prefix results and seeds expose stability information, but reused trades and
  shared screening are not independent samples or statistical confidence.
- Universe-based allocation remains an explicit known design limitation. A
  future opportunity-based policy requires a separate numerical contract,
  fair comparisons and native financial regression qualification.
- A bounded loss/return objective with robust rejection is not a guarantee of
  profitability, a global optimum, or autonomous trading authorization.
- Installation, publication and live trading are separate from development
  qualification. This change does not modify the installed broker session.

## Broken-test repair

The production audit added the `_owned_report` keyword, but five test wrappers
still had older signatures. Synthetic test wrappers now accept the keyword and
reject any fabricated ownership token; the native wrapper forwards it unchanged
to the genuine audit. Report identity and financial assertions remain intact.
The formerly affected runtime/native ownership selection passes 87 tests, and
native-calibration/contract checks pass 54 tests. No assertion was removed to
make the failures disappear.

## Local qualification of the final batch

- 154 fast decision, recipe, chronology, capital, durable-attempt and shard
  recovery checks pass. A stale shard-publication failure injection now targets
  the actual owned-artifact publication path; its original checkpoint and
  durable-attempt assertions remain.
- 10 complete-program orchestration checks pass, covering both exploratory and
  longer-history programmes, all 408 proposals, constraint feedback, baseline
  fallback, exact recovery, standalone verdicts and resealed decision tampering.
- 220 focused UI checks pass, with TypeScript and Biome validation. After the
  final capital-label clarification, all 75 Findings tests pass again. These
  suite counts overlap and are not an additive unique-test total.
- The final actual frozen 146-stock/1,869-signal experiment completes all 408
  proposals with 332 native simulations/attempts, inside the unchanged ceiling.
  Its raw historical selection still earns ₹6.60. The automatic higher-cost
  challenge loses ₹40.45, so the recorded outcome is `no_useful_improvement`.
  All four neighboring accounts pass; that does not override the cost failure.
- Capital planning runs one account, records policy/ticket/evidence blockers
  and stops further increases. It does not claim a verified starting capital.
- The actual result is saved and reloaded in an isolated private artifact area,
  and its standalone selected report carries exactly the same verdict. Completed
  recovery uses zero new native accounts. This is an isolated engine/evidence
  qualification, not a claim of a new browser journey in the installed app.
- Static resource review found no new long-lived resource owner. The worker
  retains its fenced `with store.sessions.begin()` lifecycle; new diagnostic
  collections are bounded by the recorded account/search limits. No leak soak
  or Linux production-host qualification is claimed.

No installation, Git push, merge, release publication, broker-mode change or
trading action is performed by this development batch.
