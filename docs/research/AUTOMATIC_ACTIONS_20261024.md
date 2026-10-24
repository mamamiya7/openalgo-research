# Automatic allocation, capital and corrective actions

24 October 2026. This batch implements the three follow-up gaps identified in
the [philosophy review](DECISION_PHILOSOPHY_REVIEW_20261024.md). It adds no engine,
broker downloader, LLM dependency or relaxed validation threshold.

## Primary-source research and design choices

- [LEAN portfolio models](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/portfolio-construction/supported-models)
  construct targets from active insights. Our adaptation restricts the existing
  causal cluster/inverse-volatility allocation to contemporaneously eligible
  stocks and held positions. An inactive source-universe name cannot reserve a
  slice of the account. This remains entry-only allocation; it does not force
  liquidation or rebalance existing holdings.
- [LEAN position sizing](https://www.quantconnect.com/docs/v2/writing-algorithms/trading-and-orders/position-sizing)
  considers fees, whole units and buying power. The new capital plan uses actual
  historical entry prices, selected percentage/risk sizing, costs and a declared
  planning buffer. Native execution remains the authority for actual fills and
  constraints. A whole-share estimate is not a claim of minimum capital.
- [Freqtrade configuration](https://www.freqtrade.io/en/stable/configuration/)
  separates available capital, concurrent-trade limits and dynamic stakes. Our
  implementation similarly separates cash shortage from a position, liquidity,
  risk or data constraint. Cash and risk targets can be reduced automatically;
  hard account limits cannot be increased by the optimizer.
- [RD-Agent](https://github.com/microsoft/RD-Agent) provides a research,
  development and evaluation feedback pattern. Here the permitted responses are
  explicitly registered and bounded, rather than allowing an LLM to change
  financial controls or invent missing evidence.
- [Optuna constrained TPE](https://optuna.readthedocs.io/en/stable/reference/samplers/generated/optuna.samplers.TPESampler.html)
  remains the existing search engine. Named feasibility feedback and all
  original admission, sample, concentration, cost and chronology gates remain.

These are application-specific adaptations of documented practices, not a
claim that one industry-standard algorithm supplies an optimal account amount.

## What changes

New editable v16 launches explicitly request `automatic_actions_v1`. Their
execution-search v7 offers original allocation and a new active-opportunity mode.
Both use the same registered screening count and full 408 proposals. The
512-account ceiling and 64 lost-attempt limit remain intact. Legacy v5/v6 search,
allocation and capital-planning v1/v2 retain their frozen behaviour.

The active cohort uses actual scheduled entries, prior available return
observations and existing holdings. Duplicate signals do not duplicate a
symbol's budget. Excluded and initially unaffordable tickets do not dilute
other opportunities. Source order and simultaneous-entry handling remain
deterministic. Cash, whole shares, costs, strategy and symbol exposure, open
positions, stop risk, turnover, participation and correlation limits remain
native checks. The separate constraint audit recomputes the claimed cohort.

Capital planning v3 keeps the selected account's percentages and risk target,
rather than silently substituting the original fixed rupee ticket. Before its
capacity experiments it freezes a requested-capital account, an affordability
estimate, and permitted smaller-size variants. Exploratory runs retain four
reserved accounts; chronological programmes retain three.

Recorded whole-share failures route to the declared larger account. Cash or
percentage clipping routes to smaller cash and risk targets. Missing records,
position ceilings and liquidity exclusions remain explicit while supported
records continue. There is no loop that repeatedly adds funds to defeat a
position limit. Unsupported native audit evidence fails closed.

The operating point is chosen automatically on the same opportunity cohort by
the frozen capacity criteria: fewer cash/affordability/percentage constraints,
more admitted requests, less capital and stable option order. Profit never
selects the amount or size. Unresolved exclusions remain visible, and verified
complete funding remains a separate stricter claim.

## Validation and recovery contract

Every capacity experiment uses the real account evaluator and unchanged native
constraint audit. Saved plans, settings, ledger-derived receipts, sequence and
stop conditions are recomputed on recovery. Reopening a completed programme
must need no additional account calculations. New editable defaults propagate
through the server saved-setup launch path as well as the browser builder.

A capacity-tested operating point does **not** silently replace the performance
winner. Changed capital or sizes need the unchanged performance, cost and
chronological gates before performance promotion. The original research verdict
and historical-evidence ceiling remain visible beside the automatic capacity
choice. This avoids presenting validation at one account amount as validation
at another.

## Qualification

Small tests cover the actual causes and remedies, active allocation in both
native execution paths, unchanged legacy contracts, saved-setup requests,
full-budget orchestration and tamper-resistant recovery. Full native programme
and platform results are recorded in STATUS after qualification; passing pure
planning tests alone is not installation or production acceptance.

Resource review: new arrays and receipts are bounded by the existing source,
strategy and three/four-account limits. No connection, thread, executor,
unbounded cache or external model session is added.
