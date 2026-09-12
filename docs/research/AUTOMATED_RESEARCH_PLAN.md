# One-click strategy research

**Proposed product contract, 12 September 2026. Not implemented.** Requested after
the chosen-setup increment. This specifies the automatic mode of Optimize within
OpenAlgo Research; it is not another application or a replacement calculation
engine. [DELIVERY_PLAN.md](DELIVERY_PLAN.md) owns implementation order.

## Product outcome

From a signal CSV and the account's saved research assumptions, one click should
run a bounded research process and answer:

> Which trading rules and observable conditions produced the strongest repeatable
> evidence for these scanner signals, where did they struggle, and is the
> improvement worth retaining?

An unchanged baseline, an inconclusive finding, or no reliable improvement is a
valid result. The product does not promise a profitable setting or exhaust every
possible mathematical rule. The complete automatic protocol is new work; existing
Optimize currently searches user-specified trading settings.

## Current foundation and actual gap

Current source supports seven numeric axes: target, stop, holding sessions,
holding minutes, trailing distance, order size and allocation. Not every axis is
active for every strategy. Optuna uses TPE or grid and one scalar objective:
return, negative drawdown, or return minus drawdown. The fresh UI starts at 25
proposals; the adapter fallback is 50, capped at 1,000. Suggested ranges are local
multipliers around existing settings, not inferred strategy research bounds.

Native research currently supports long NSE cash-equity signals, daily or minute
execution, frozen shared-account calculations, retained studies/candidate reports,
later-period evaluation, comparisons, decisions and chosen setup reuse. It does
not execute RSI/EMA/Bollinger/relative-strength/regime filters from the research
request. Its price planner currently requests no indicator warmup. Native
independent benchmark reporting, general walk-forward and durable study extension
remain planned. A chart indicator elsewhere in OpenAlgo is not evidence that the
research worker can reproduce that indicator over historical signals.

Sources: [Optuna adapter](../../research/connectors/optuna_portfolio.py),
[builder](../../frontend/src/components/research/PortfolioBuilder.tsx),
[connector contract](CONNECTORS.md), [current status](STATUS.md).

## User journey

1. **Upload or reuse signals.** Infer supported instruments, dates, observation
   times, duplicate rows and the required execution interval. Reuse chosen rules
   and saved capital/cost assumptions where available. First-use assumptions use
   a named, visible research preset; they do not claim suitability for the user's
   real account. Ask only for essential information the source cannot establish.
2. **Research automatically.** One primary action launches the complete frozen
   recipe, including its final reserved-period check. A compact editable line
   shows capital, horizon, costs, objective profile and research depth. Advanced
   users retain manual search. Do not add an indicator settings questionnaire.
3. **Understand progress.** Check signals → Prepare prices → Test ideas → Check
   reliability → Findings. Show symbol/candle preparation and completed candidate
   evaluations separately. A candidate evaluated in several windows consumes
   several simulations. Leaving the page must not lose the run.
4. **Read findings.** Lead with whether an improvement was supported, the baseline,
   and at most three distinct retained alternatives. Show applicable conditions,
   failure conditions, rules, signal participation, risk and later-period results.
5. **Investigate and retain.** Drill into the existing report/study, compare, record
   Keep/Reject/Revisit and deliberately choose a setup. Preserve the baseline,
   failures and exact recipe. Reuse with new signals through the existing flow.

No automatic trading or silent replacement of the chosen setup follows a finding.

## What is being optimized

The CSV is a set of observed scanner opportunities. It does not establish the
original scanner program, unsignalled opportunities, a point-in-time market
universe or whether historical scanner observations repaint. Scanner names and
links are provenance, not executable definitions.

The first implementation should optimize **management of these signals** and
test **additional conditions on these signals**. Reconstructing or changing the
original scanner requires a separate versioned rule/universe contract. Do not
silently generate signals on dates or stocks absent from the CSV.

| Research family | Question to test | Required evidence |
| --- | --- | --- |
| No extra condition | How do the supplied opportunities perform unchanged? | Original CSV and the fixed baseline rules |
| EMA / trend | Does trading with an observable price trend help? | Preceding price history, declared MA definition and timing |
| Momentum | Does recent price change favour these entries? | A declared return lookback, known at the decision time |
| RSI | Is continuation or a pullback more useful? | Declared RSI definition; continuation and pullback are separate hypotheses |
| Relative strength | Does outperformance of the market help? | Stock and named benchmark returns over aligned lookbacks; distinct from RSI |
| Bollinger Bands | Does a breakout or a return toward the average help? | Declared bands, lookback and information cutoff; separate hypotheses |
| Market conditions | Do rising/falling trends or low/high volatility change results? | Independently sourced benchmark and causal condition definitions |
| Liquidity / intraday context | Do observable turnover or entry-time conditions help? | Historical fields actually supplied at that time; minute evidence for intraday claims |

Start with a small versioned catalog: no filter, one stock condition, and at most
one coarse market condition. Do not stack every indicator or let the search invent
arbitrary code. Add richer combinations only after their simpler components have
earned further testing. A descriptive result discovered after seeing outcomes is
exploratory; it becomes an executable filter only through subsequent validation.

Keep capital and transaction costs fixed as assumptions. Start with TP/SL, active
holding and supported trailing variants; investigate sizing/allocation separately
under fixed exposure constraints so extra risk is not confused with better signals.

## Frozen research protocol

### 1. Prepare data once for the allowed experiment

Persist the recipe, supported families, ranges, decision timing, evaluation
periods, benchmark, costs, acceptance policy and budget before scoring. The data
planner takes the union of feature warmup, eligible signal windows and maximum
allowed holding requirements. Cache derived feature series by version and source
identity. Trials cannot start new downloads.

Historify supplies matching stored candles; missing coverage goes through native
OpenAlgo history and the connected broker, then is saved/read back in Historify.
Daily features on daily signals remain daily. Minute signals use only completed
information available before entry; a morning decision cannot see that day's
closing price or final volume. Date-only observations retain next-session entry.
Fitted regime thresholds use development data only. Old reports are unchanged.

Missing prerequisite history disables the affected hypothesis with an explicit
reason. It must not silently substitute prices or treat missing indicators as
zero. If a blocked family is essential to the declared recommendation protocol,
stop or return incomplete findings; do not claim that all requested checks ran.

The new contract must carry each instrument's exchange/venue explicitly: current
research acquisition uses the NSE equity path, which is not a generic index
benchmark route. Preserve missing-volume provenance before admitting liquidity
filters; an existing zero default cannot prove observed zero volume. Keep feature
warmup separate from scored observations because current validation slicing drops
pre-period history. Respect the native bar-by-lot and checkpoint limits when
planning windows and budget. These are required data/engine changes, not settings
that the current frontend can simply turn on.

### 2. Establish references and eligible opportunities

Run the unchanged settings on the master CSV with matching capital, period,
calendar, price and execution assumptions. Keep data-coverage exclusions fixed.
An intentional filter rejection is a distinct recorded event from missing data.
Filtered alternatives still compare on the same observation timeline and master
opportunity set; preserve idle cash, admitted/rejected signals and turnover.

Use three references: unchanged strategy, explicit idle-cash assumption, and a
named broad-market buy-and-hold benchmark supported by the asset profile. Show
price-return versus total-return basis. Broad-market context is not a matched
execution comparison. Do not construct a supposedly investable historic basket
from the eventual CSV membership, silently substitute an unavailable index, or
change an old study's objective when adding a reporting benchmark.

### 3. Allocate chronology before search

Proposed new-mode preset: latest 20% of eligible **sessions** reserved for a final
check; earlier sessions supply expanding development/validation windows when
sufficient history exists. This is a new versioned recipe, not a reinterpretation
of existing saved reservations. The percentage is a product preset to calibrate,
not a confidence guarantee. Minimum history, completed outcomes and independent
market-date groups constrain the number of windows and allowed model complexity.

Never randomly split signal rows. Remove training outcomes whose holding windows
cross the next validation boundary using the largest allowed horizon. Prior
observable prices may warm up indicators; later outcomes cannot fit them. Start
each test window with fresh capital and no carried positions under an explicit
reset policy. Carrying positions is a separate future contract.

Nine months of CSV signals cannot acquire earlier scanner opportunities merely
by downloading older prices. Many stocks signalling on one date share market
exposure. Report short-history or narrow-regime findings as exploratory even when
the signal count is large. Previously inspected/reserved periods retain their
exposure history; the application cannot certify that data was unseen elsewhere.

### 4. Search with a finite budget and a declared selection rule

Test coarse hypotheses first, refine promising parameter neighbourhoods second.
Use Optuna conditional categorical/numeric spaces and the existing native engine
adapters. Exhaust small finite grids only when the entire declared grid fits;
otherwise report sampled configurations and actual work, never "all combinations".

Calibrate an initial standard budget around 100–200 proposals, with independent
caps on all simulations, wall time, memory and feature preparation. This is a
proposed engineering starting point, not a runtime or statistical guarantee.
Record seeds and all failed/repeated/rejected proposals across the research idea.
Budget exhaustion returns the available state; it must not extend itself until
it discovers a profitable candidate. Prune only on comparable completed validation
observations; a weak early market stretch alone is not a generic pruning signal.

Rank using development-validation outcomes with explicit feasibility gates for
activity, sample support, exposure, concentration and risk. Zero-trade/near-cash
results can be valid references but cannot win a strategy-improvement claim simply
because their drawdown is zero. Preserve old scalar rankings; introduce a new
versioned selection policy. Prefer a stable parameter region and simple rules over
an isolated peak. A small return/risk trade-off set can use native Optuna
multi-objective support; it does not choose the user's financial preferences.

### 5. Challenge finalists, then consume the reserved period

Before the final check, run bounded neighbouring-parameter, higher-cost/slippage,
supported delay and concentration/weak-window tests. Resampling must preserve
market-date dependence and describe whether it resamples recorded returns or
re-runs a portfolio; naive shuffled trades do not preserve shared-account effects.
Selection-bias statistics require their own checked assumptions and observations;
never manufacture an odds-of-future-success score from a heuristic score.

Freeze the finalist set and development-based recommendation order, then execute
the originally authorized reserved-period evaluation once. Show supportive and
poor results alike. Do not tune, add a condition or re-rank finalists using this
period while still describing it as an independent final confirmation. Record
that the period was used; subsequent improvement work needs new evidence.

Nautilus replay may investigate supported execution assumptions for finalists.
Agreement between two engines using the same data is not independent market
validation. Unsupported engine semantics stay unavailable.

## What appears in the result

The finding should distinguish **development selection**, **later-period check**
and **exploratory observations**. Show dates and actual evidence rather than a
generic "AI confidence" badge.

- Recommended rules, unchanged baseline and meaningful alternatives; a clear
  no-improvement/inconclusive result when appropriate.
- Where it held up: observable trend/volatility/relative-strength conditions,
  results by window and the number of independent dates/trades supporting them.
- Where it weakened: losing conditions, concentration, costs and unstable ranges.
- Net return, drawdown, benchmark context, exposure, turnover, trades, signal
  participation and sensitivity; unsupported numbers stay unavailable.
- Existing report charts/trades and Optuna history, importance, slice/contour and
  trade-off views only when their underlying observations exist. Importance is
  specific to that search, not proof an indicator caused returns.
- Exact recipe, sources, feature definitions, assumptions, considered families,
  skipped checks, versions and study lineage in saved details/export.

Illustrative wording only, not an actual result: "These scanner signals were more
consistent with a rising-market filter and a four-session hold. The improvement
was concentrated in moderate-volatility periods; later-period evidence was
inconclusive." Actual rules, numbers and dates must support every such statement.

## Delivery dependencies and acceptance

These are refinements of existing packages, not separate competing milestones:

| Build order | Existing work | Acceptance before enabling the next step |
| --- | --- | --- |
| Freeze protocol and recover work | D1/D2, M3 | Recipe identity separated from budget; persisted stages, retries and compatible resume; no duplicate download/evaluation |
| Establish evaluation and references | D4/R3, M4a/M4b | Native benchmark/warmup contracts, chronological windows, cohort/account comparison and no-improvement gates; leakage and boundary regression cases |
| Automate supported trading settings | M3/M4b | One-click fixed-rule baseline/search/challenge/final-check journey using current supported execution rules; honest incomplete state |
| Add causal conditions | M5.1/M5.2 | Pinned indicator definitions and warmup, timestamp cutoff, conditional search, filter attribution, missing-series handling and deterministic replay |
| Deliver conditional findings | F1, M4b, report R3–R5 | Stable-region and stress evidence, consumed-period history, readable findings, save/choose/reuse and rejection paths |

One-click execution-settings research may ship as a clearly narrower intermediate
mode. Do not label it automatic indicator/regime discovery. The full request is
complete only when an eligible CSV can traverse these stages, including a case
with no reliable improvement, through the ordinary OpenAlgo installation.

## Reuse and primary references

OpenAlgo/Historify owns data; VectorBT computes supported indicators and simulates
the shared account; Optuna proposes/searches configurations; optional Nautilus
investigates supported execution models. Our contribution is the recipe, evidence
and native user journey. Upstream tools provide these building blocks, not an
automatic trading-research policy for arbitrary CSVs.

- [Optuna 5.0 search spaces](https://optuna.readthedocs.io/en/v5.0.0/tutorial/10_key_features/002_configurations.html): conditional parameters and the growth in search difficulty.
- [Optuna multi-objective search](https://optuna.readthedocs.io/en/v5.0.0/tutorial/20_recipes/002_multi_objective.html): objective trade-offs; the application still defines its decision policy.
- [VectorBT indicators](https://vectorbt.dev/api/indicators/basic/): MA, RSI, bands and reusable indicator machinery; validate against the locally pinned version.
- [QuantConnect walk-forward](https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization): chronological retraining as a workflow reference.
- [Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) and [Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf): repeated-selection bias; reference methods are not already implemented metrics.

Prepared using two independent specialist reviews (systematic-trading protocol and
current code capability), Graphify retrieval and direct source verification. This
planning review ran no broker downloads, optimizations, orders or app deployment.
