# Portfolio-wide allocation — 21 October 2026

New automatic studies use `automatic-hypotheses-v16`. This adds one account-wide
allocation alternative alongside the existing allocation, exits, timing and
stock/market choices. Saved v15 and earlier studies keep their numerical recipes.
The execution contract is `research-execution-aware-v5`, native VectorBT adapter
v6 / `vectorbt-joint-causal-bars-v6`. Nautilus does not inherit these semantics.

## Allocation and comparison

The original allocation remains the reference. The alternative uses exactly 60
returns from 61 preceding completed daily closes. Returns must share the same
calendar and be available before entry. Sample covariance is shrunk 10% toward
its diagonal. Connected components of positive **regularized** correlation at
least 0.8 form clusters; each cluster receives an equal part of the existing
gross-exposure ceiling. Within a cluster, inverse-volatility weights divide its
budget. Negative correlation receives no offsetting-exposure credit.

The universe is the frozen source's symbols, not a historical market membership
claim. The largest aligned calendar cohort is used, with deterministic calendar
tie-breaking. Missing, misaligned or constant series are explicitly unavailable;
they are never assigned zero volatility. A new or held symbol missing the
required aligned observations prevents that entry. The implementation never
inverts covariance, so perfectly correlated series do not require an unstable
matrix inverse. No return forecast, Kelly estimate or sector metadata is invented.

New whole-share entries are clipped against both symbol and cluster budgets,
using opening equity after prospective entry friction. Already held lots of the
same symbol count together across strategies. Original strategy/order ceilings,
cash, gross/symbol exposure, open positions, stop risk, daily buy turnover and
admitted broker-volume participation still constrain the native fill. Exits
remain unrestricted. This is entry allocation, not continuous rebalancing;
appreciation may take an existing position above its entry cap.

The allocation axis is shared across the account, not multiplied per strategy.
One allocation probe replaces one existing screening probe; allocation_50 remains
available adaptively. The ceiling stays **408 proposals / 512 account attempts**
across the same six fits. Matched sizing controls restore original allocation as
well as original cash sizing. Capital, costs and risk ceilings stay fixed. All
chronological, cost, constraint, neighbour, symbol/date and seed vetoes remain;
final-period outcomes cannot select or rerank settings.

## Frozen evidence and native reconciliation

Allocation points bind their entry session, prior dates, availability, source,
weights, clusters and unavailable symbols. The existing frozen feature receipt
adds these points only for the new policy. Opening marks from the frozen price
snapshot cover all admitted execution timestamps, including alternative minute
entry clocks. They are audit inputs, never predictive features. Marks are
bounded to 500,000 observed symbol/timestamp cells and the complete feature
receipt retains its 64 MiB bound; larger unsupported inputs fail explicitly.

The audit reconstructs actual ordered native buys/sells, cash and held quantities
against those frozen opening marks. It independently verifies equity and held
symbol/cluster exposure before checking share capacities. Understated exposure
with a consistently enlarged reported capacity is rejected. Exact saved resume
retains the original recipe and immutable histories; changed declarations cannot
silently attach to another recipe's offline seed programme.

## Runtime and qualification

[Runtime was profiled before widening the search](PORTFOLIO_RUNTIME_20261021.md).
Repeated evidence serialization/validation and report statistics were material
costs; checkpoint disk writes were small in the measured prefix. The search
budget therefore has not increased. A matched warm-account comparison measured
0.726 s for the previous policy, 0.725 s for new-policy original allocation and
0.742 s for cluster allocation. This small two-symbol fixture had nonbinding
allocation caps; original orders, summary and equity curve were exactly equal.
These timings are not a promise for larger daily or minute workloads.

Component checks cover binding native fills, duplicate buys, fees, turnover,
singular dependence, unequal volatility, constant/missing series, causal future
price changes, exact resume, tampering and changed intraday entry clocks.
The pure allocation builder's 100-cycle resource check retains no cache or
external resource; handles stay flat and memory plateaus.

400 distinct backend checks and276 frontend checks pass. Two separately declared
complete native v16 programmes pass: independent noise retains original settings,
and persistent edge supports changed settings while an adverse final period
cannot rerank the selection. Both verify genuine audits and exact pause/resume;
636 account requests contain634 native calculations and two exact report reuses.
This is two generated datasets, not the previous v15 five-model/three-programme
qualification and not general false-discovery control. The full controlled-account
service journey separately verifies storage, seed vetoes, exact resume and
altered-evidence rejection. Saved selected cluster allocation replays with equal
native orders and no broker access. See [STATUS](STATUS.md) for activation.

The complete native runs preserve a frozen source digest. Subsequent changes
only fix metadata/report readers; their separate tests verify v16 coverage,
75-dimension reconstruction and the unchanged old v15 coverage identity. A
source comparison verifies all other calculation files remain unchanged.

Broader sector/factor controls, continuous rebalancing, executable capacity,
larger independent calibration samples and new asset accounting remain future
work. This policy is an additional tested hypothesis, not a universal optimum.
