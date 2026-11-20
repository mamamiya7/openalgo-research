# Optional OpenAlgo Rust engine

Development addition, 15 November 2026. Not part of the published preview.38 ZIP.
VectorBT remains the default and the calculation engine for automatic research.
The v16 programme remains 408 proposals within 512 accounts, with its existing
validation gates. No automatic plan is redirected to the preview engine.

## Installation and selection

From this checkout, run the optional installer with the application's Python:

```powershell
.\.venv\Scripts\python.exe tools/install_openalgo_backtest.py
.\.venv\Scripts\python.exe tools/install_openalgo_backtest.py --check
```

The SDK 2.2.0 overlay lives under ignored `research_data/openalgo-backtest-sdk`.
It reuses the app's installed dependencies in a separate calculation process.
The main `openalgo==2.0.5` requirement and environment are unchanged. Setup
refuses to overwrite a nonempty incompatible overlay. `--check` exercises a
native Rust import offline. A missing installation remains visible with a setup
reason under portfolio **More settings → Backtesting engine**.

Select **OpenAlgo Rust (preview)** explicitly for a fixed backtest or Custom
Optuna search. Unsupported settings are rejected; no fallback or silent changes.
Existing saved reports retain their engine, SDK, adapter and policy versions.

The retained SDK overlay is the sole extra runtime package. It is reused across
runs and can be removed after the optional engine is retired and no pinned
replay depends on it; saved reports remain readable without an installed engine.

## Supported contract and differences

| Area | Optional connector policy |
|---|---|
| Inputs | Frozen daily NSE cash-equity signals and OHLC; no broker client or network in the calculation worker. |
| Positions | One native shared CNC account. Different symbols share its cash. Repeated same-symbol signals must have disjoint maximum potential holding windows, across all strategies and all searched holding values. |
| Entry and sizing | Next-session opening; explicit whole shares, allocation and ticket caps marked using only opening prices. Native estimated entry charges are included in affordability. No default two-percent sizing buffer. |
| Holding exit | Opening of entry-session index plus `hold_sessions`. VectorBT exits at that session's close. These policies can produce different returns. Stops and targets use the native SDK's own matching and tick rules. |
| Charges | Dated Zerodha CNC statutory charges, brokerage and DP treatment. Flat `cost_bps` overrides must be zero. This is an explicit engine-specific charge policy, not a claim to match the connected broker's rates. |
| Settlement | Native CNC settlement limits free buying cash. Queued opening sales are not credited to simultaneous entry requests. Equity charts show settled cash, not free buying power. |
| Metadata and prices | Supplied native instrument metadata; historical OHLC is never rounded to make it fit. All traded symbols need common frozen coverage over the executable account window. Sparse per-signal coverage is rejected, without downloading extra data. Current master ticks do not attest to historical tick rules. |
| Exclusions and tails | Existing preparation exclusions remain frozen. Excluded-only symbols do not enter the SDK. Unsupported overlaps reject the request, never exclude or merge signals. Snapshot tails stay open. |
| Unsupported | Automatic research, execution-aware/ATR policies, entry conditions, trailing stops, minute/timed trades, flat cost/slippage overrides, shorts, derivatives and live execution. |

The adapter identity is `openalgo-portfolio-adapter-v1` and execution policy is
`openalgo-daily-opening-delivery-v1`. Optuna remains the existing optimizer;
SDK `bt.optimize` is not substituted for our search. Existing chronological
partitioning, candidate validation, budgets and exact saved replay remain in
the native orchestration layer.

## Accounting and saved evidence

The SDK exposes accrued per-fill/per-trade costs and a separately settled daily
contract note. These differ due to daily tax rounding. Preserve both raw native
tables. Attribute each settled note's paise to its day's fills proportionally to
accrued statutory costs, using stable largest remainders; retain native DP charges
on their original fill. Trade and strategy attribution must reconcile against
native daily account equity before publication. This attribution rule does not
change engine equity or relax any financial validation gate.

Small engineering tests cover shared cash, protective targets, affordability,
disjoint and overlapping repeated signals, explicit deadline-price differences,
native settled-cost reconciliation, chronological Custom search, queued jobs,
immutable saving/export and reopening with recalculation forbidden. Native tests
run against the optional overlay on Windows; the compatibility workflow installs
and runs these checks in slice 0 on Windows and Linux. Linux CI is configured,
not claimed as locally executed. Private real saved-data comparison receipts live
under ignored `.agent-native/openalgo-engine/`; they are qualification evidence,
not proof of a profitable strategy.

The process boundary strips application credentials and blocks network and child
process creation. A bounded input/output file protocol, execution timeout,
cancellation callbacks, reaped worker, closed log and scoped temporary directory
own resources. No database or broker client is opened by the calculation worker.

Upstream SDK source:
https://github.com/marketcalls/openalgo-python-library/tree/fae72d6af7e3206503f1b6dfb05bed72d3a45633

