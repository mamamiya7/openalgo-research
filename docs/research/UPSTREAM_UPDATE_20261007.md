# OpenAlgo host update — 7 October 2026

## Host integration

The previous imported host was `f5165699dfa3925d750c9cf2ef73d586b62611ba`.
This update integrates upstream main at
`ee26e4e24caeeb68a27a5417bf3744bc0c5e1964`: 50 intervening commits,
including generated-asset commits. The latest published OpenAlgo release and
the host's visible version remain **2.0.2.6**; these additional main changes
are **unreleased**, identified by their exact commit and dependency locks.
This is not a new public Research release.

[Complete upstream comparison](https://github.com/marketcalls/openalgo/compare/f5165699dfa3925d750c9cf2ef73d586b62611ba...ee26e4e24caeeb68a27a5417bf3744bc0c5e1964)
· [Published releases](https://github.com/marketcalls/openalgo/releases).

The reviewed source delta covers 210 upstream paths excluding generated
`frontend/dist`. Assets are built from the combined host and Research source,
not copied from upstream's host-only build. Existing native Research screens,
connectors, automatic-hypotheses-v4 and historical recipe versions remain.

## Changes available in the updated host

| Area | Imported changes | Benefit to Research |
|---|---|---|
| Charts | `openalgo-charts` 2.5.1 → 2.6.0; 112 built-in visual studies, higher-timeframe options, grouped chart objects and chart transformations | Better native visualization; not automatic admission of 112 optimizer conditions |
| Trading workspace | Comparison scales, data window, quick entry, resizable grids, context actions and chart-wide undo | Native tools remain available at `/trading`; saved Research charts retain their private frozen feed |
| OpenScript | Browser and Python engine 0.5.0 → 0.8.1 together; instrument/session/date and lot-sizing support, compiler diagnostics and strategy-book fixes | Future selected-setup export can use a tested common host; saved recipes are not recompiled or deployed during update |
| Native backtest views | Results-first layout, equity/drawdown tabs, whole-run curves, clearer marks, same-bar reversal rendering and mark cleanup | Reuse the native curve renderer and cleanup patterns when expanding Research reports |
| Broker history | Angel requests use IST independently of machine time zone; ISO candle offsets convert correctly to UTC epochs. Shoonya fixes cash-symbol search and invalid OHLC/volume candles | Cleaner newly acquired broker candles through the existing History/Historify facade |
| Streaming/orders | Firstock, Pocketful and RMoney batch/reconnect/watchdog fixes; Angel ambiguous-response reconciliation and unique Kotak order tags | Host reliability fixes; the Research worker still has no order capability |
| Assistant/notifications | Remembered model selector, ChatGPT model/catalogue updates, LiteLLM threading and chart-tool fixes; WhatsApp notify contract corrections | Optional host features, not prerequisites for backtesting or optimization |
| Dependencies/docs | Axios 1.20.0, PyJWT 2.15.1, Tornado 6.5.10, urllib3 2.8.0 and LiteLLM 1.104.0; chart/OpenScript documentation and test refinements | Locked, reproducible combined installation |

## Research adaptation and data continuity

The Research saved-chart wrapper uses the real 2.6.0 ReplayController and
continues replaying **observed completed bars**. Simulation is deliberately
omitted: OHLC alone cannot establish the order of intrabar ticks. Backward
seeking still hides future candles, markers and indicator values. Native
crosshair subscriptions follow the updated unsubscribe contract. Chart objects,
markers, replay timers and indicator guards have matching disposal paths.

Existing reports, frozen inputs, account configuration and Historify storage
are retained. No new Historify/history API schema migration is required in
this upstream interval. There is no second market-data database, wholesale
redownload, report regeneration or change to live/sandbox mode. Newly fetched
Angel/Shoonya data can intentionally differ because of upstream corrections;
exact saved Research replay continues using its original frozen evidence.

The calculation pins remain OpenAlgo SDK 2.0.5, VectorBT 0.28.5,
Optuna 5.0.0, NumPy 2.4.4 and pandas 2.3.3. New browser chart indicators
and OpenScript behavior do not silently change those calculation contracts.
Windows OpenScript test imports use local pytest module names so Python's
standard-library `test` package cannot shadow the project helpers.

## What to reuse next, in dependency order

1. **Richer saved-chart inspection.** Add native candle/indicator readouts and
   confirmed higher-timeframe visualization against the same frozen bars.
   Keep this inside the trade chart; avoid a second analysis screen or broker
   fetch when inspecting an existing result. Missing warmup/volume must remain
   explicit rather than invented.
2. **More optional chart studies.** Evaluate Volatility Squeeze, Elder-Ray,
   ZLEMA, VIDYA, Schaff Trend Cycle and 52-week range as visual studies first.
   Supply enough recorded history and keep execution markers on original
   candles. Indicator-name overlap alone is not numerical parity.
3. **Whole-period report curves.** Adapt Research's normalized equity and
   drawdown series to the host renderer, keeping full-run extrema, original
   drawdown signs and correct seconds/milliseconds conversions. Avoid another
   chart library or changing saved report statistics.
4. **Selected setup → OpenScript study.** Only after Python/chart/OpenScript
   numerical parity and causal-bar tests, export a preview from the exact saved
   settings. Installing, scheduling and trading are separate user actions.
5. **Further optimizer conditions.** Continue benchmark trend/volatility and
   stock-filter admission using broker/Historify evidence, versioned Python
   definitions, missing-data rules and unseen-period checks. New chart studies,
   repainting pivots and simulated ticks are not search inputs by default.

## Verification and installation scope

Production TypeScript/Vite build; scoped Research/chart/trading/Historify UI
tests; native 2.6.0 browser canvas, EMA/RSI and completed-bar forward/backward
replay; Research frozen-chart/context/cache tests; isolated broker-history,
OpenScript, streaming, assistant, History-source and notification contracts.
Tests use isolated databases and controlled market data; broker/order tests
use stand-ins. No real order, scheduled strategy or broker login is exercised.

Activation is guarded by an idle owned Research worker, unchanged reviewed
source hashes, retained configuration and saved-job identities. Reviewed source,
locked Python dependencies and matching frontend assets are installed together
with an ignored rollback snapshot. Startup verifies existing-account setup,
worker heartbeat and protected-route boundaries. This is a targeted host update;
it does not require rerunning an unrelated full Windows installer workflow.
