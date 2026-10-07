# Research connector contract and implemented scope

Checkpoint: 9 September 2026. These are implemented adapters, not a proposed
catalog. The native consumer workflow builds a joint portfolio, uses OpenAlgo's
historical data and delegates calculation to the selected engine. Current
acceptance is recorded separately in [STATUS.md](STATUS.md).

## Implemented components

| Component | Tested dependency | Implemented responsibility |
| --- | --- | --- |
| `vectorbt_portfolio.py` | VectorBT 0.28.5 | Native `Portfolio.from_order_func`, one shared cash pool, independent signal lots and reconciled strategy attribution; daily and one-minute execution. |
| `nautilus_portfolio.py` | NautilusTrader 1.231.0 on Linux x86-64, Python 3.12 | Native `BacktestEngine`, one INR cash account, separate strategy/position identities, native fills and PnL; daily and one-minute inputs under a distinct OHLC event policy. |
| `optuna_portfolio.py` | Optuna 5.0.0 | Seeded TPE or grid search over strategy settings and allocation axes, using the selected joint engine. |
| `research/portfolio.py` and `services/research_portfolio.py` | Native OpenAlgo services | Request/capability checks, interval and window planning, source ownership, price preparation, jobs and saved results. |
| `portfolio_coverage.py` and `portfolio_validation.py` | Native research modules | Fixed signal cohort, explicit missing-window exclusions and chronological later-period evaluation. |
| `services/research_mcp.py` | Existing OpenAlgo MCP and API authentication | Bounded, account-scoped research tools using the same portfolio service. |

New consumer runs default to VectorBT; Optimize defaults to Optuna TPE. The
Nautilus choice is available through More settings when its runtime is available.
Unsupported settings block submission with an actionable explanation. There is
no automatic fallback or silent strategy modification.

`vectorbt_adapter.py`, `optuna_adapter.py` and the original scanner evaluator
remain for older saved workflows. Their single-strategy, daily-only or grid-only
constraints do not describe the new portfolio adapters. Their recorded policy
and exact evidence must not be rewritten to match a newer engine.

## Shared request and result boundary

The portfolio request has a version, starting capital, selected engine and one
to eight signal strategies. Each strategy has a stable ID, name, original saved
`source_id`, allocation, validated trade settings and optional search ranges.
Combined portfolio receipts are not reusable raw signal inputs. Browser upload
and the owner-scoped saved-source picker both preserve the original source ID.

The calculation interface is:

```python
evaluate(strategies, snapshot, capital, *, progress=None)
```

Each normalized strategy contains `id`, `name`, `allocation_pct`, `config` and
`signals`. Every source row retains its strategy identity. Validation slices may
leave a strategy with no signals in one period; it remains represented with zero
contribution. An entirely unusable portfolio fails clearly.

`snapshot` is a checked native daily or one-minute snapshot with sessions,
OHLC, provenance and the needed calendar/timing fields. Nautilus also requires
native instrument metadata. Adapters validate the supplied inputs and never
call a broker or download another series. Capabilities/configuration can be
checked before numerical package import or price preparation.

A common report includes `summary`, `equity_curve`, `ledger`, `per_strategy`,
exact evaluated strategy settings, coverage, execution/policy metadata and native
engine records. Summary includes net return, maximum drawdown and closed trades.
Contribution is percentage points of initial total portfolio capital. A
strategy equity curve is attribution against its initial allocation, not a
standalone backtest or a reserved cash account. Unavailable measurements must not
be replaced with invented zeroes.

## Shared account and data semantics

- Long NSE cash equities only, whole shares, strict fills, signal order within
  strategy order. Scanner-count filters, alternate priority algorithms, shorts,
  margin, leverage, derivatives and corporate-action cashflows are unsupported.
- All strategies use one native engine account. Allocation caps deployed capital
  as a share of opening marked account equity. Signal order size is a share of
  that allocation budget. Total allocations must be greater than zero and at
  most 100%; unused capacity remains account cash.
- Opening exits settle before opening entries. Within-bar and closing proceeds
  cannot fund earlier entries. Repeated same-symbol signals retain independent
  lot/strategy exit rules.
- Date-only observations enter next session; timestamped observations enter at a
  scheduled minute open strictly after observation, subject to trade settings.
  Daily rules remain daily; intraday timing or minute holding selects one-minute
  prices. A mixed account uses the finest required interval for every strategy.
- Native Historify is read first at that interval. Missing required prices go
  through OpenAlgo's ordinary connected-broker history service and are persisted
  and read back there. Frozen research evidence is derived from that same archive.
- The planner considers the maximum possible holding window across search axes.
  Trials use one frozen snapshot and one fixed eligible signal cohort. A missing
  required candle after a successful broker request excludes the affected window
  from every candidate, with original signals and reasons retained. Failed broker
  requests stop preparation and preserve resumable progress instead.
- No missing candle, mark or future session is manufactured. Incomplete snapshot
  tails may remain pending/open. All-excluded input does not become a successful
  zero-return backtest.

## VectorBT execution policy

Adapter: `vectorbt-portfolio-adapter-v1`.
Policy: `vectorbt-joint-causal-bars-v1`.

Native `Portfolio.from_order_func` owns fills, fees, order records, cash and
assets across independent lot columns with cash sharing enabled. The adapter
translates signal rules and verifies that native lot cashflows plus marked
assets reconcile to the native total on every bar. It never calls the original
scanner evaluator for accounting.

Daily and one-minute inputs support fixed stops, targets, trailing, intraday
session close, multiday session/minute deadlines, fees and configured slippage.
Protection applies on the entry bar. Ambiguous stop/target bars use stop-first;
completed-bar highs update trailing for subsequent bars. Thresholds retain the
recorded policy rather than applying legacy two-decimal rounding. Daily and
minute execution can differ because their available chronology differs.

The old `vectorbt-daily-next-open-v1` adapter's protection-after-entry-day policy
remains unchanged for its saved results. This is an intentional, tested policy
difference, not a silent numerical upgrade.

The joint adapter rejects more than 25,000 signal lots or 500,000 bar-by-lot cells.
The public portfolio contract remains limited to eight strategies. These are
admission bounds, not speed or capacity guarantees. Progress supports
cancellation; numerical import/compilation may delay the next callback.

## Nautilus execution policy and runtime

Adapter: `nautilus-portfolio-adapter-v2`.
Policy: `nautilus-ohlc-low-first-v1`.
Instrument eligibility: `nautilus-supplied-grid-windows-v1`.

The connector drives a real native INR CASH HEDGING account. It creates distinct
native Strategy IDs and Position IDs for signal lots, including repeated symbols.
Native fills, commissions and position PnL reconcile with native account cash and
marked positions. No VectorBT evaluation or custom account engine supplies its
results.

OHLC inputs become explicitly modelled zero-spread quote/control events in
`Open -> Low -> High -> Close` order. Native stop/limit matching uses those events.
They are not observed ticks, known intrabar crossing times or market depth.
Stops round downward and targets upward to supplied tick increments. Commissions
round at INR precision. A stop can therefore fill differently from VectorBT:
in the tested open-100, stop-95, low-90 entry bar, Nautilus fills the native stop
market order at the modelled low quote of 90; VectorBT uses its 95 threshold.
The saved policy makes this difference explicit.

This adapter supports fixed stops/targets and fees but rejects trailing stops
and configured nonzero slippage. Each entering instrument needs supplied INR
currency, lot size 1, tick size and price precision. Missing metadata is not
replaced with guessed values. Before trials, v2 checks raw OHLC over each signal's
maximum allowed holding window against the supplied tick grid and precision.
Affected signal windows receive stable exclusions; unrelated prices outside those
windows do not reject the portfolio. An unavailable master entry can exclude its
signals; an entirely unsupported cohort fails clearly. Original broker prices
are never rounded to fit the instrument grid. OpenAlgo's current master provides
the supplied specification, not proof of the historical tick grid. Earlier v1
reports retain v1 identities and require their recorded adapter for replay.

Nautilus has a separately locked Linux runtime because its tested dependencies
conflict with OpenAlgo's core PyArrow pin. The tested Windows integration uses
Linux through WSL; a local direct Windows native import hung in bounded probing
and is not enabled by this adapter. This is not a claim about all upstream
Windows installations. The side runtime is part of the distribution's calculation
setup, not another web service or account.

Bounds are 2,500 signal lots, 100,000 bar-by-lot cells and 200,000 model events.
The subprocess boundary limits input/output size and runtime, sanitizes its
environment, propagates progress/cancellation and cleans up processes. See
[NAUTILUS.md](NAUTILUS.md) and [RUNTIME.md](RUNTIME.md) for exact tested behavior
and runtime setup.

## Optuna search and exact recovery

Adapter: `openalgo-optuna-portfolio-v1`.

`TPESampler` and `GridSampler` make real seeded Optuna proposals. Each distinct
feasible candidate invokes the selected joint portfolio engine. A repeated
configuration reuses its exact score; invalid allocation totals are pruned,
not normalized. The budget counts proposals, including repeated/pruned ones.
TPE starts with a feasible lower-corner trial, has ten startup proposals and
then adapts to measured scores.

Search axes are strategy-specific target, stop, session/minute holding limit,
trailing distance where supported, per-signal size and allocation. Axes must
match active supported controls. Objectives are net return, net return minus
maximum drawdown, or negative maximum drawdown. These are research selection
criteria, not claims about future performance.

The portfolio limits are 1,000 trial proposals, a 50,000-combination grid,
10,001 values per axis and a 32 MiB checkpoint. Execution is serial and seeded.
Checkpoints retain compact trial settings/scores and one full winning report,
not repeated price matrices. Resume binds the snapshot, strategy definitions,
capital, search settings and exact adapter/engine/optimizer versions. Serial
replay restores sampler state through pinned Optuna APIs. Incompatible identity
or versions block resume instead of starting a different study silently.

Optuna Dashboard is not connected: the in-memory study plus research checkpoints
is not an Optuna RDB storage installation.

## Later-period checks, replay and handoff

A later-period check splits on ordered signal dates before selection. It needs
at least five distinct dates and an earlier share from 50% to 90%. Search sees
only the earlier signal and price slice; both normalized and raw price maps are
trimmed. Potential holding windows crossing the boundary are excluded. The
selected settings are simulated separately on later signals with fresh capital
and no positions carried from training. This does not claim a general rolling
walk-forward implementation for the new joint adapters.

Exact trial replay reads the saved selected settings and frozen prices, performs
no new acquisition or search, and preserves source identity. A validation run's
selected-trial replay uses its original earlier period, not an expanded period.
Opening a saved report does not recompute it. Evidence retention and backup
follow original input/result references and price chunks.

The existing native MCP transport exposes capability/source/run listing,
preview, CSV upload, queue, result/trade pages, cancel/resume, exact trial replay
and selected strategy-definition export. Verified native account ownership and
explicit remote research scopes apply; old grants do not gain new scopes.
[MCP.md](MCP.md) defines tool and response bounds. Definition export has execution
disabled and is a research handoff only; no paper/live order path is installed.

## Verification boundary

Native engine tests cover cash competition, independent same-symbol lots, fees,
attribution, no retroactive funding, entry/deadline timing, incomplete data and
cancellation. Review regressions cover frozen cohorts, holdout price isolation,
exact replay, resume identity and evidence retention. Controlled native history
integration demonstrates service/data alignment, not universal broker coverage.
The full VectorBT fixed run, 25-proposal TPE/later-period check and exact selected
replay complete with explicit outcomes. The minute run's 61 required candles match
Historify; an execution optimization preserves its financial results. Fresh Linux
installation and an idempotent populated reinstall pass. Nautilus v2 has 29 real
native and 10 metadata regressions. Its full-input calculation also completed
through the Windows/WSL boundary, with all 1,995 frozen daily candles matching
Historify. Local preview acceptance is complete for the declared scope. Docker
and cross-version migrations are unverified. Consult
[STATUS.md](STATUS.md) for the current acceptance boundary.
