# Native OpenAlgo Research

Current implementation: 9 September 2026, `0.1.0-preview.4`. The native joint
portfolio workflow, full VectorBT/Optuna user-input checks, exact replay and fresh
Linux installation/reinstall are verified. A fresh Windows directory and locked
environment also passed the account, VectorBT, Optuna, replay and restart journey.
Real Fyers acquisition of an absent minute window and reuse of daily Historify
prices are verified separately. Full-input Nautilus v2 calculation
also completed through the native Linux bridge. Docker and cross-version
migrations remain unverified beyond the targeted, idempotent NSE calendar update.
The product connects established simulation and optimization tools in one
installable OpenAlgo distribution. The implemented scope is recorded in
[CONNECTORS.md](CONNECTORS.md); acceptance and unresolved work are in
[STATUS.md](STATUS.md). [SYSTEM_MAP.md](SYSTEM_MAP.md) explains what the user
receives and which layer owns each responsibility.

## Product commitment

Ship one versioned **OpenAlgo Research** distribution with a native **Backtest &
Optimize** screen, the research services and bounded worker, VectorBT and Optuna
adapters, and an optional NautilusTrader calculation runtime. Use the ordinary
OpenAlgo account, broker connection and database services. Maintain explicit host
and dependency compatibility so the distribution can be updated alongside
OpenAlgo and its calculation libraries. Do not require another research website
or broker account. See [DISTRIBUTION.md](DISTRIBUTION.md).

The user's central requirement is to reuse existing products, not build another
general backtesting engine or optimizer. External engines own order execution
and account calculations. Optuna owns search. Our layer expresses a portfolio of
signal strategies, translates their requirements, prepares native data, manages
jobs, and presents useful results. A connector must declare its actual supported
semantics; it does not automatically make arbitrary strategy programs portable.

The earlier scanner implementation is retained for saved evidence and compatible
legacy reruns. It is not the calculation backend for new joint portfolio runs.
Earlier ideas to embed the old application's Streamlit interface or retain its
FastAPI server are superseded.

## Broker data is fundamental

Broker historical data is the reason for moving this work into OpenAlgo. Read
the installation's matching-interval Historify prices first, fetch missing
required coverage through OpenAlgo's ordinary history service and the connected
broker, persist and read back those prices in Historify, then freeze the exact
calculation input. There must not be a second research broker downloader or a
silent fallback to public-file prices.

Use the broker connected to the installation; do not hard-code Fyers or require a
second setup. Availability still depends on that broker's instruments, supported
intervals and history. Complete stored coverage and saved-result review do not
require a current broker token. A controlled response or public-price fixture is
not proof of a real broker download. The full fixed native-price run now
completes, as do its bounded optimization and later-period check. The full-input
Nautilus v2 calculation also completes with explicit instrument and price
exclusions. Local preview acceptance is complete for this bounded scope. See
[STATUS.md](STATUS.md) for the exact verified scope.

Infer required data from all uploaded strategies, their execution settings and
the complete allowed optimizer holding-window range. Date-only signals use daily
candles under daily execution rules; stops, targets and trailing alone do not
force a minute download. Timestamped signals, intraday horizons, minute holding
limits and entry/exit clocks require one-minute candles. A mixed portfolio uses
the finest required interval across the account. Download only missing required
symbol windows, once for the experiment, not once per trial.

Use the native NSE calendar with a reviewed 2025–2026 admission baseline. New
portfolio calculations need only their signal and holding period, with no legacy
scanner warmup. Special sessions and chosen entry times must fit the planned
window. Reject unsupported years or unconfirmed required special-session hours
before downloading. Calendar upgrades preserve administrator edits and other
exchanges; completed reports retain their exact older calendar evidence.

Preserve native OHLC values, original timestamps and instrument identity. A
successful broker request that returns missing candles can cause fixed signal
exclusions across the whole search, with original observations and reasons
retained. A request failure stops price preparation and retains resumable
progress. Missing prices cannot prove an untouched stop or justify an invented
exit. Incomplete calendar tails can remain open or pending under the recorded
policy; a wholly unusable input must fail clearly.

## Consumer experience

The primary journey is: open Research, upload or reuse saved signals, add strategy
rules and allocations, choose Backtest or Optimize, run, and review results.
Support one to eight long NSE cash-equity CSV signal strategies, date-only or
timestamped observations, intraday or multiday holding, and automatic resolution.

Use a calm native interface with relevant controls. Strategy details belong in
settings drawers; search controls appear in Optimize; advanced engine selection
belongs in More settings. Default to VectorBT and Optuna TPE. Offer the bounded
Nautilus adapter when its runtime is available, and block incompatible settings
without silently changing the request. Selecting a saved source must not start a
price download or duplicate an added strategy accidentally.

Show the combined account summary and chart first, then each strategy's
contribution, trades, trials and readable settings. Keep saved runs easy to
reopen. Detailed receipts, policy identifiers and repeated diagnostic narration
belong in exact exports and developer documentation. Surface short actionable
errors and material exclusions; keep healthy operation quiet. Use native
components and restrained transitions that respect reduced-motion preferences.

## Portfolio and research semantics

All strategies share one actual engine cash account. Each strategy retains its
own identity, source rows and rules, even when it trades the same symbol as
another strategy. Its allocation is a maximum deployed share of opening account
equity; each signal's budget is its configured share of that allocation. These
are caps, not separate cash balances. Whole-share sizing and strict fills apply.
Opening exits may fund opening entries; later proceeds may not retroactively
fund earlier orders. Strategy order followed by CSV order resolves competition.

Preserve engine-specific execution policies and test intentional differences.
VectorBT's joint policy includes protection on the entry bar and causal trailing.
Nautilus owns native matching under explicit OHLC-derived event assumptions.
Daily bars and minute bars provide different chronology and may produce different
fills. Do not label modelled OHLC events as observed ticks. Reconcile strategy
PnL attribution from native fills and positions to the combined account delta.

Optuna TPE or grid searches supported per-strategy settings and allocations
through the same joint engine contract. Check allocation constraints rather than
normalizing invalid candidates. Freeze price evidence and signal exclusions
before scoring candidates. A later-period check selects settings using only the
earlier period and evaluates them separately on later signals with fresh capital
and no carried positions; boundary-crossing holding windows are excluded.

Every result retains original signals, normalized strategy definitions, price
snapshot, execution and adapter versions, coverage findings, settings, search
seed/state and result identity. Reopening or exporting must not download or
recalculate. Exact trial replay uses the recorded configuration and prices;
interrupted studies resume only with compatible identity and versions. Exporting
a selected strategy produces a research handoff definition with execution
disabled, not an installed paper or live strategy.

## Native architecture

- React/TypeScript screens use OpenAlgo's components, navigation, API client and
  authentication. Browser drafts and cached queries are account-scoped.
- Flask routes and services validate requests and resolve capabilities, data
  requirements, ownership, jobs and results inside the host application.
- Historify remains the mutable native candle archive. Research SQLAlchemy
  metadata and immutable ignored artifacts record source and calculation evidence.
- The bounded background worker runs expensive calculations outside the
  broker-facing web process. Progress, cancellation, leases, checkpoints and
  atomic result publication use the research job infrastructure.
- VectorBT and Optuna use the locked research dependencies. Nautilus uses a
  separately locked Linux runtime through a bounded process boundary so its
  dependencies do not force changes to OpenAlgo's core environment.
- Research MCP uses OpenAlgo's existing transport and verified account identity.
  Explicit research read/write scopes protect remote actions; no new control
  server or trading permission is introduced. See [MCP.md](MCP.md).

## Limits and verification

The implemented interface accepts CSV signal strategies, not arbitrary native
engine code. Shorts, derivatives, leverage, margin, corporate-action cashflows,
observed-tick replay, Optuna Dashboard integration and automatic paper/live
handoff are outside this release scope. Nautilus additionally rejects trailing
stops and nonzero configured slippage. Its v2 adapter freezes maximum-window
eligibility against supplied instrument metadata before trials; it does not round
raw prices or claim that the current master proves historical tick grids. Upstream engine capabilities are not
claims about this connector.

Verify hand-checkable native fills, shared cash, fees, attribution, daily/minute
timing, fixed cohorts, holdout boundaries, exact replay and cancellation/restart.
Test data ownership, real Historify persistence, bounded MCP responses and native
UI journeys. Run applicable OpenAlgo resource checks and distinguish Windows,
Linux, controlled integration and real broker evidence. Public readiness requires
a usable full user-input workflow and the selected distribution's fresh setup,
update and recovery checks; a passing unit suite is not that acceptance.

Keep credentials, live databases, original private signals, research artifacts
and machine-specific receipts out of Git. No live-order action or publication is
implied by research development. User-authorized main-app testing is recorded
separately; it does not authorize scheduled strategies or trading-mode changes.

## Historical baseline

This development checkout began from reviewed OpenAlgo main commit
`c6a431401872691f3d46e0bb840a3637e18b0591`, host version 2.0.2.2, from
https://github.com/marketcalls/openalgo . The 6 September scanner milestones and
later modular connector work have distinct policies and evidence. Preserve their
saved reports rather than rewriting their numerical history.
