# Market conditions and adaptive research

Updated 16 September 2026. Product extension requested from the supplied regime-trading
tweet and diagram. This joins the [automatic research plan](AUTOMATED_RESEARCH_PLAN.md)
and [delivery order](DELIVERY_PLAN.md); it is not another application.

**Implemented through conditional replay:** saved reports now retain native
daily index context and offer **Test condition** for a known trend or volatility
cohort, including small samples for exploration. The worker gates original
signals before entry and reruns the whole VectorBT cash account on frozen prices;
the saved report shows the original-versus-conditioned portfolio comparison.
Unknown conditions are excluded, existing exclusions are preserved, and exact
replays retain the condition. No request-thread calculation or repeat price
download is required. This is exploratory testing on viewed data, not automatic
condition selection or new untouched validation. Broader search, condition-aware
saved setups and shadow/live controls remain pending.

## Outcome

Upload scanner signals and choose Automatic. The computer should determine the
required native history, test a bounded set of plausible conditions and trading
rules, and return the strongest supported scenario or an explicit insufficient
evidence result. The output must explain the chosen scenario, comparison,
unseen-period result, sensitivity and next action in a compact report. It cannot
promise the best possible strategy or identify future failures with certainty.

The supplied architecture is a useful direction, not a validated specification.
Trend, volatility, liquidity and stress overlap. A rising market can also have
high volatility. A top-decile reading is a relative observation, not a 90%
probability of a crisis. A range-bound label does not prove mean reversion.
Five transformations of the same close series are not five independent votes.

## Where this belongs

OpenAlgo owns accounts, broker connections, the ordinary history service,
Historify, calendar and eventual execution controls. Our research layer owns
causal data requirements, immutable recipes, jobs, validation and presentation.
VectorBT owns portfolio simulations and supported numerical indicators. Optuna
owns bounded conditional search. Nautilus remains an optional execution-model
cross-check only for the settings it actually supports. A language model may
explain recorded results; it must not invent measurements or choose live risk.

## Data and signal contract

| Signal family | Source and evidence needed | Initial treatment |
| --- | --- | --- |
| Trend, momentum, efficiency, realized volatility, drawdown | Native daily Nifty 50 closes, recorded calendar, 150 completed sessions | Implemented descriptive context; 160 preceding sessions requested once |
| Stock trend / relative strength | Continuous native stock history plus aligned index history available before entry | Next feature extension, not inferred from sparse execution windows |
| Hurst exponent | Explicit price/return definition, estimator, scale range, uncertainty and sufficiently long history | Optional research diagnostic after estimator validation; not a default strategy selector |
| India VIX / realized versus implied volatility | Native instrument support, point-in-time India VIX observations and matched annualization/horizon | Capability-gated future input; daily OHLC is not implied volatility |
| Volatility term structure | Observed expiry-specific contracts/quotes, expiry/roll definitions | Not supplied by a single spot VIX index |
| Cross-asset dependence / breadth | A declared historical universe, common timestamps, survivorship and corporate-action policy | Separate capability; selected scanner hits cannot stand in for the market universe |
| Credit spreads / rates slope | Correct local-market instruments and point-in-time publication/revision records | Separate connector required; no US proxy or latest-revised macro backfill silently substituted |
| Liquidity / market impact | Observed volume, spreads, depth and execution evidence | Unavailable in auxiliary OHLC v1; zero-filled volume is not an observation |

Read existing Historify coverage first. Download only missing daily windows
through the connected broker's ordinary OpenAlgo history service, then persist,
read back and freeze them. No alternate downloader, synthetic scanner signals or
minute downloads merely because a feature needs warmup. If the admitted calendar
does not cover the required earlier years, stop with its specific coverage error.
If the broker returns incomplete history, retain missingness and unknown labels.

Every input needs instrument/exchange, observed timestamp, available-at time,
source/revision, units and freshness. Historical computations use the information
available at that decision, including macro publication lags. Future values,
whole-sample normalization and backward-smoothed state probabilities are excluded.

## Dependency order and acceptance gates

### 1. Historical conditions — implemented in the development copy

An explicit **Analyze market conditions** action in the saved tear sheet prepares
Nifty daily context, records its fixed recipe and saves a separate analysis.
It describes saved trades; it does not alter the optimizer or the original run.
The native queue retains acquisition progress; reopening, parameter-chart changes,
backup/restore and exact exports reuse pinned evidence.

Recipe v1 uses standard native VectorBT SMA20/60, 20-session momentum,
20-return realized volatility, 20-session directional efficiency and 60-session
close drawdown. Volatility and drawdown percentile ranks use exactly 90 strictly
earlier feature observations, with neutral tie handling. The resulting window is
150 completed closes. Trend and volatility are separate dimensions; a stress
flag needs high relative volatility plus elevated drawdown. These fixed thresholds
are descriptive heuristics, not fitted probabilities or a crisis detector.

Every saved trade is attributed using only closes available before its recorded
session opening. Intraday entries must fall inside the recorded session. Display
classified/missing coverage, distinct entry dates, average net trade return and
win rate. Simultaneous stock hits are not independent dates. Cohort P&L describes
existing fills; deleting signals changes cash and capacity, so it cannot be sold
as a filtered portfolio backtest. At least 30 trades and 20 distinct entry dates
per compared cohort are required even to surface an observed research lead;
positive chronological halves are an additional screen, not statistical proof.

The earlier and already-published later periods are reported separately. A
reserved unpublished final period is never fetched or used to pick conditions.
No current market badge or live position recommendation is emitted from a
historical report. The browser should show one finding and next step, two compact
trend/volatility strips, and a small table. Methodology stays in details.

### 2. Executable hypotheses and actual filtered native replay

Extend the versioned strategy contract with a small allowed causal condition set,
not arbitrary generated strategy code. Freeze allowed feature families, thresholds,
condition count, history requirements and baseline before any search. Apply a
condition at entry only; preserve separate rules for any later exit/resize policy.
Replay the entire joint cash account through VectorBT with unchanged cost/fill
assumptions, rather than subtracting filtered trades from old results. Save exact
trade membership and unavailable-feature exclusions across comparable proposals.

Acceptance: hand-checkable entries, shared cash/capacity, strict feature timing,
maximum holding-window purging, bounded cancellation/resume and exact replay.
Compare the unchanged strategy, price-condition filter and cost/delay stress.
Nautilus confirmation is optional and must declare unsupported features.

### 3. Automatic conditional search and robust scenario advice

Use Optuna categorical/conditional proposals for permitted regimes, stock features
and trade-management rules. Bound feature families and combinations instead of
claiming exhaustive discovery. Freeze a compute budget and record every attempted
family, failed proposal, reused calculation and multiple-testing exposure.

Select using chronological development folds, minimum distinct entry sessions,
sample/exposure constraints, costs, turnover and a simple unfiltered baseline.
Require a stable neighbourhood rather than only the highest score. Commit the
candidate before the untouched final evaluation. A later-period disappointment
must not trigger tuning on that same period. Add rolling walk-forward and explicit
research-cycle identity before repeated adaptive fitting.

Report: **Keep baseline**, **Worth paper-testing**, **Mixed evidence**, or
**Insufficient evidence**, with exact scenario, sample, baseline delta, risk,
cost sensitivity and final-period result. Optional explanations must cite the
saved measurements. Never label a hypothesis a proven strategy or an optimum
outside the tested search space. Save to the existing studies/shortlist/decision
journey; no second library.

### 4. Shadow monitoring and proposed adjustments

After the historical gate passes, introduce an explicit opt-in paper/shadow mode
with current strategy identity, declared universe, data freshness and a frozen
policy. Daily inputs update after a completed exchange session. A four-hour clock
cannot create four-hour information from daily closes. Intraday monitoring needs
its own native interval contract, market-session calendar and independent tests.

Classify with hysteresis/persistence and an unknown/stale state; test false alarms,
whipsaw, missed events and decision latency. Independent hard risk limits must not
wait for the classifier. Compare proposed changes against a no-change shadow book
after realistic costs. Correlations and regime sample estimates are uncertain;
position sizing must consider concentration, cash, drawdown and liquidity caps.
Do not call a noisy point estimate Kelly-optimal. Evaluate capped fractional/risk-
constrained sizing against simpler fixed/volatility-target rules first.

### 5. Execution and risk controls — separate delivery gate

Only a separately approved account policy may authorize order generation or
automatic de-risking. This needs idempotent orders, current holdings reconciliation,
partial fills/rejects, instrument capability, stale-feed behavior, restart recovery,
operator override and audited hard risk limits. A crisis label is never the sole
kill-switch input. Define whether stopping new entries, cancelling orders and
closing positions are separate actions; opening hedges can add risk.

Test these controls in paper mode and fault scenarios before live rollout.
This research increment neither starts a schedule nor changes orders, positions,
live/sandbox mode or existing strategies.

## Records and context exports

Canonical state stays in versioned metadata and immutable JSON artifacts, not
mutable Markdown that a strategy trusts as an order instruction. Future optional
human-readable exports can include `signals.md`, `current-regime.md`,
`regime-history.md` and `position-adjustments.md`, derived from the same exact
records. Include as-of/expiry, source and recipe identity, unknown inputs, decision
and audit links. A stale file must never authorize a trade. Keep these private
account records and broker credentials out of Git.

## Primary evidence behind the design

- [NSE: India VIX](https://www.nseindia.com/static/products-services/indices-indiavix-index)
  defines expected Nifty volatility over the next 30 calendar days; this is not a
  directional prediction or a term-structure dataset.
- [Cboe: VIX futures](https://www.cboe.com/tradable-products/vix/vix-futures/)
  distinguishes futures/expiries from the index.
- [Diebold and Inoue: Long Memory and Regime Switching](https://www.nber.org/papers/t0264)
  shows that regime changes and apparent long memory can be confused; a Hurst
  cutoff is not enough to assign a strategy automatically.
- [Busseti, Ryu and Boyd: Risk-Constrained Kelly Gambling](https://stanford.edu/~boyd/papers/kelly.html)
  adds an explicit drawdown-risk constraint to growth optimization. It does not
  remove uncertainty in estimated trading outcomes.

The implementation choices and gates above are our design proposals, not claims
that these sources validate this particular scanner strategy or classifier.
