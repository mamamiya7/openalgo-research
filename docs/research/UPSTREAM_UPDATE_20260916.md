# OpenAlgo upstream update and research reuse plan — 16 September 2026

**Installed in the normal Windows application.** The combined build, 2,653 frontend tests, 485 targeted upstream checks and populated-database migration/preservation checks pass. Existing account settings and 57 research runs are preserved. Full distribution acceptance is recorded by the compatibility workflow for each published commit; see [STATUS.md](STATUS.md). Feature reuse listed below is a plan unless explicitly described as existing native functionality.

## Exact comparison

- Research release before upgrade: `51da45e1006c5ef03dc3b64d89fd71d1e6331fbb`.
- Actual embedded OpenAlgo base: `c6a431401872691f3d46e0bb840a3637e18b0591`, 4 September 2026. It reports platform **2.0.2.2**, but already contains some work later described in 2.0.2.3 release notes.
- Fetched target: `d858c2384cbd6a9bdb09d0fb42c1a08b46c48c29`, 16 September 2026, platform **2.0.2.5**, with additional commits after that release. In particular it contains chart-engine 2.3.2, beyond the 2.2.0 described by the 2.0.2.5 release note.
- Exact range: 157 commits; 122 after excluding merges and automatic frontend-build commits. Diff spans 649 paths, including generated assets and extensive tests/docs.
- Three published platform release notes reviewed: 2.0.2.3 (6 September), 2.0.2.4 (11 September), 2.0.2.5 (14 September). Git code diff is the authority for what is actually new to this installation.
- Graphify existing graph was queried first for research, Historify and chart relationships. Its useful entries include frozen `market_series`, saved condition replay and HistorifyCharts, but it predates this upstream delta. New upstream facts below were verified directly against the pinned target rather than asserted from the older graph.

## What was already present

Charts and indicators are **not entirely new**: the base already has `/trading`, indicator picker/settings, custom JavaScript indicators, multi-pane charts and `openalgo-charts` **1.9.2**. The change is a much richer and more reusable implementation, now also used by Historify and other surfaces.

Likewise, the new Strategy Module/RMS and pure risk core described in the broad 2.0.2.3 release notes were already in this base. `services/risk/` and `services/strategy_module/` have no changes in this exact range. Do not sell them as newly integrated research capabilities. Shared history service, Historify acquisition service and Historify database implementation also have no changes in this range; the new Historify feature is chiefly the charting frontend and its feed adapter.

## Release sequence

- **2.0.2.3:** the part new to our actual base introduces the Agent, chart-side conversation and provider configuration, the terminal's 2.x upgrade and bottom dock/armed trading behavior, real Kotak historical data, and the Docker base-image change. Its broad release notes also describe Strategy/RMS work that our base already contains.
- **2.0.2.4:** chart loading/replay lifecycle, profiles and Objects panel; SDK 2.0.5; Upstox V3/history/tick-size and Kotak streaming/account fixes; GTT history; CORS/date validation/shutdown/security improvements.
- **2.0.2.5:** voice surface and persisted spoken journeys, order tools opt-in, phrase retirement and migrations, all 85 drawing tools, feed subscription batching/cache cleanup and a stronger shutdown fix.
- **After 2.0.2.5, through the pinned 16 September target:** shared Historify chart rebuild ([97fdeae16](https://github.com/marketcalls/openalgo/commit/97fdeae16)); chart engine 2.2.1 → 2.3.2, stream-driven candle repair ([a7e6e1261](https://github.com/marketcalls/openalgo/commit/a7e6e1261)); instrument arithmetic and live combined charts ([f31a7cd5a](https://github.com/marketcalls/openalgo/commit/f31a7cd5a), [56983ed7b](https://github.com/marketcalls/openalgo/commit/56983ed7b)); Strategy Builder and agent premium charts on the shared engine; live Strategy Builder chart with optional underlying ([ee0900e2e](https://github.com/marketcalls/openalgo/commit/ee0900e2e)); Kotak carry-forward cost/entry-price clarification. These changes are included by commit pin, even though the platform version still reads 2.0.2.5.

## Grouped inventory of meaningful changes

| Area | Actual updates in this range | Relevance to Research |
| --- | --- | --- |
| Chart engine and trading terminal | 1.9.2 → 2.3.2; new data-loading controller; stale-response and replay isolation; stream-driven candle repair; full drawing tool metadata/editor including 85 tools; pane Objects controls; branding; TPO and session-volume-profile chart modes/settings; drawing persistence compatibility; copy/save and chart behavior refinements | Strong display/review foundation. Reuse the engine and shared wrapper instead of another chart package or local drawing implementation. Trading terminal behavior remains distinct from saved research replay. |
| Historify charts | Complete rebuild on shared `OpenAlgoChart` and `ChartGrid`; downloaded-symbol catalog; valid stored-source-aware timeframe choices; custom minute/hour/week/month/quarter/year intervals; seven layouts up to eight panes; persisted per-pane layouts; read-only feed, date-window paging, timezone-edge padding and Windows epoch guard | Lets the user inspect the same local prices Research prepares. Explicit link from data coverage to native Historify is an immediate useful journey. A frozen saved report must use its own saved-artifact feed, not silently reread mutable Historify. |
| Instrument arithmetic and combined charts | Symbol search accepts arithmetic over instruments; correct search of the currently typed leg; history folds aligned leg data; combined chart stays live with one LTP stream per leg | Useful future relative-strength and spread/combined-premium exploration. Visual expressions are not an execution model for synthetic instruments or an authorization to expand the current long NSE cash-equity engine scope. |
| Strategy Builder charts | Strategy and multi-strike OI tabs use OpenAlgo chart engine; primary data feeds make indicator calculation work on the actual combined premium or underlying; dated windows page; live strategy updates; underlying hidden by default | Native options presentation can be reused if/when instrument-aware options research is admitted. These are sampled series, represented honestly as flat OHLC bars, not observed option OHLC. |
| OpenAlgo Agent | New `/agent`, chart-side agent rail, `/agent/config` under Admin; provider/model settings and secrets; threaded persisted conversations; edit/copy/retry; live account/history/options/instrument tools; generated strategy/Flow tools; typed visualizations, payoff/combined-premium charts; Python indicator catalog; optional ChatGPT subscription authentication and other providers | Optional future explanation layer over structured saved research evidence. It should call existing research services with explicit actions, cite saved results and have read-only research tool exposure initially. It is not a replacement optimizer, causal classifier, or numerical validator. |
| Voice agent | Spoken surface shares same assistant/tool flow; screen visualizations, persisted transcripts, voice thread listing, idle microphone timeout, improved failure/retry messages; optional order read-back flow; retired approval phrase removed | Lower priority than a clear screen journey. No agent/voice provider setup or live trading should be silently enabled by this upgrade. |
| Orders and account presentation | GTT history in order book plus sandbox support; sandbox timestamps normalized to IST; fill timestamps and per-fill IDs preserved; position row says when broker average is not entry price; Kotak carry-forward value uses cost rather than overnight mark; Kotak holding average and Groww tradebook rupee fixes | Useful later for real-paper/live reconciliation. Historical research trade reports must continue to represent native simulated engine fills, not these live book rows. |
| Broker history and price correctness | Kotak Neo now has real historical service for NSE/BSE/NFO/BFO and indices, with interval support and shared pacing/retry; MCX/CDS explicitly unsupported for its history. Upstox V3 migration/CAS/shared rate limiting; daily candle date uses IST; tick size normalized to rupees; prev_close populated | Automatically benefits our existing broker-agnostic native acquisition path when that broker is connected. Does not establish real-broker acceptance for every broker or expand our supported assets by itself. Upstox and Kotak are good next capability/regression fixtures. |
| Feed reliability and performance | Kotak SFeed and per-data-centre routing/unavailable-value decoding/consistent LTP; bounded 5paisa snapshot cache; batched 5paisa XTS subscriptions and correct unsubscribe; Tradejini request coalescing; Flattrade reconnect/close leak fixes; Samco stops retrying rejected tokens; shared depth-key compatibility | Better native streaming, especially large watchlists. These are streaming subscription improvements, not a replacement for our bounded historical download planner and cache reuse. |
| Platform quality/security | Central CORS policy used consistently; date-range checks shared between timing/holiday calls; SIP validation before fetching; backtester accessibility labels; GEX formatting; ordered dev-server shutdown stopping schedulers/health/background DB writers; Docker failed-build handling; dependency advisory patches and test/docs coverage | Keeps research installation maintainable and reduces restart contention. Host holiday API's accepted 2020–2050 range does not prove a complete exchange calendar for research; retain reviewed research session admission. |

## Runtime, dependency and migration implications

1. **Platform 2.0.2.5; Python SDK 2.0.5; chart package 2.3.2.** These are independent version numbers. Python remains >=3.12 and the frontend Node engine requirement is unchanged. Native research/VectorBT/Optuna/Nautilus pins must be preserved and reconciled with upstream lock changes rather than replaced by a base-only environment.
2. New application dependencies: `litellm==1.99.0`, `agno==3.0.5`, `ddgs>=9.16.0`. New frontend dependencies include the OpenUI packages, react-markdown and remark-gfm, plus a shared zustand override. Security-related changes include GitPython, maplibre-gl, colord, svgo and Vitest. Install from merged manifests/locks, not just changed source files.
3. Upstream Docker stages move from Debian bullseye to trixie; our research fork had already moved its Python stages to bookworm and now adopts trixie too. Container compatibility must be rechecked; local Windows acceptance alone does not certify it.
4. New registered migrations are `migrate_agent.py`, `migrate_agent_voice.py`, `migrate_agent_voice_phrase_removal.py`. The first creates six `ag_*` tables and checks schema drift; the second adds absent voice settings without replacing explicit choices; the third removes two retired keys. They do not require a research schema migration and must preserve saved research evidence.
5. **Migration invocation detail:** the phrase-removal script interprets a relative SQLite URL against the process working directory; other agent migration scripts explicitly anchor it against project root. Use a verified absolute `DATABASE_URL` or execute in a context known to point at the normal installation DB. Do not blindly rely on `cd upgrade` for a relative default URL. The release acceptance must verify the actual database path before any migration runs.
6. `app.py` now imports/registers the Agent blueprint, initializes its DB and registers shutdown handlers. Merge with research route/worker integration rather than taking either side wholesale. Production still has the eventlet + one-Gunicorn-worker constraints; the chart/frontend work does not remove them.
7. Agent order tools are opt-in on new/default configuration; voice and voice trading are pinned off unless chosen. Preserve explicit existing settings and never enable any of these as a side effect of an upgrade. Scheduled strategies must not be executed merely to test the updated app.
8. Existing chart drawings/layouts need compatible restore; the new engine includes migrations, but our research evidence is separate and must not be rewritten by chart preference restore.

## Indicator reuse: distinguish visual studies from engine decisions

- The generated chart catalogue at target lists **102 JavaScript chart indicators**, across momentum, trend, volatility and volume. Examples include RSI, EMA, Bollinger Bands, ATR, ADX, VWAP, Hull MA, Supertrend and historical volatility. These are studies drawn by `openalgo-charts`.
- The Agent Python registry describes **127 `openalgo.ta` callables: 114 indicators plus 13 utilities**, with statuses/aliases and explicit failures for some methods. The registry prose was measured against SDK 2.0.3, whereas the pinned target SDK is 2.0.5; revalidate any adopted function against the installed SDK rather than treating prose as current mathematical proof. Flow's catalog is different again because its node contract cannot express every multi-series calculation.
- The Python dispatcher is an attractive reusable foundation: pure computation over a caller-supplied frame, typed parameter coercion, explicit input/output shapes, warmup sizing, second-series requirements and guarded unsupported functions. Its current cleaning assumptions and rounded outputs must be reviewed before using it for threshold-sensitive research gates. Research must preserve gaps/availability and exact series provenance; do not fill missing historical candles merely to make a chart study look continuous.
- A study shown on a chart is not automatically part of a saved strategy or an Optuna search. To use it for trading decisions, save a versioned feature specification, compute on frozen native input in the bounded worker, enforce warmup and session-close availability, lag features to actual entry time, bound the search, and evaluate the frozen choice on untouched data. A visually attractive indicator cannot bypass those steps.
- Special care: pivots/fractals/divergence and other retrospective chart patterns may repaint or require future confirmation. They need availability-time semantics before optimizer admission. A JavaScript/Python indicator name match is not proof that kernels, defaults or values match.

## Reuse priorities and journeys

| Priority | Native feature to reuse | Research journey | Integration boundary / acceptance |
| --- | --- | --- | --- |
| P0 | Host routes, migrations, dependency locks, shutdown | Upgrade an existing installation and reopen saved work | Preserve account, broker settings, stored prices and research evidence; prove runtime compatibility before installing. |
| P1 first | Shared `OpenAlgoChart`, indicator picker/drawings, feed contract | Results → Trades → View on chart → inspect entries/exits → visual replay | Feed only frozen report prices; no order handlers, live feeds or broker calls; future information hidden at replay cursor; display studies do not alter results. |
| P1 next | Historify catalog, charts, supported timeframes | Data coverage → View prices → return to experiment | Explicitly inspect current native storage; do not substitute it for saved result evidence or download on opening. |
| P2 | Python indicator registry metadata and pure dispatcher | Automatic research → causal feature selection → bounded search → untouched evaluation | Validate SDK math, warmup, input gaps, parameter identity and availability before entry; save the recipe and keep unseen data outside selection. |
| P3 | Expression feeds and multi-pane layouts | Compare stock/benchmark or compatible combined series | Align frozen input series; sampled or synthetic visual bars are not observed fills or new execution support. |
| P3 | Agent service tools and structured chart rendering | Ask why a saved trial/condition performed differently | Optional, read-only saved-evidence tools initially; numerical engines retain authority; no automatic trades or new runs. |
| Later | Strategy Builder premium/OI charts and existing risk core | Options research and paper/live handoff | Requires separately supported instruments, costs, execution semantics and explicit operating scope. |

### P0 — compatibility and preservation, this upgrade

Merge the complete upstream host update, resolve research registrations/dependencies/security workflows, rebuild frontend, test distribution/runtime plus research acquisition/saved-report/optimization/replay regressions, migrate the normal host with backup, and retain user configuration and saved research data. Record the exact upstream hash rather than just 2.0.2.5 because charts 2.3.2 is post-release.

### P1 — saved trade chart first, then native price inspection

1. Trades table offers **View on chart** in a drawer or focused screen using the shared `OpenAlgoChart` plus a new read-only frozen-artifact DataFeed. Draw original candles, entry/exit markers and available stop/target/condition context for one selected symbol/trade; no orders, live subscription, repair polling or implicit re-download. This is the first feature integration because it makes the actual saved result inspectable without creating another chart engine.
2. Enable chart studies/drawings with minimal toolbar, remember preferences per report/symbol, and show calculation inputs available for that report. Add visual bar replay over that same evidence, keeping it distinct from **Exact replay**, which reruns the numerical engine. During bar replay, price data, trade markers, overlays and indicator inputs must all stop at the cursor; hidden future trades must not leak via a marker or tooltip. Reject unsupported timeframes instead of silently aggregating or acquiring more data. Mobile and keyboard behavior are part of acceptance.
3. Research data coverage offers **View prices** → `/historify/charts/<symbol>?exchange=<exchange>&interval=<D|1m>`. This views existing mutable native storage, clearly separate from exact saved report evidence. No broker download on navigation. Preserve route back to experiment.
4. Keep summary equity/drawdown and statistical plots on the existing report contract; a full trading terminal everywhere would recreate the clutter the user has repeatedly rejected.

Useful source seams: `components/chart/OpenAlgoChart.tsx`, `components/chart/ChartGrid.tsx`, `lib/chart/feeds/historifyFeed.ts`, `lib/historify/catalog.ts`, `lib/historify/intervals.ts`, `lib/chart/intervalRegistry.ts`, and the existing research artifact/owner access service.

### P2 — causal feature registry and bounded automatic search

Use the Python indicator registry/dispatcher metadata where correct, through a small explicit research adapter. Begin with interpretable stable features: trend EMA/SMA, momentum/RSI, ATR/realized volatility, and price relative to benchmark. Preserve the existing selection/later-period separation. Feature identity, parameter bounds, warmup demand, acquisition reuse, lagging and version pins must exist before expanding Optuna's choices. Teach the result page why the selected condition helped or failed, using actual saved evaluations. The 102 chart studies are not a sensible unbounded search space.

### P3 — comparison and explanations

Reuse chart expression support for stock/benchmark ratio and eventually allowed spreads, with aligned frozen series. Add an optional read-only Agent tool to explain a saved report/compare chosen trials, giving the agent structured data and links rather than free-form access to live order tools. Tool routing must not generate new research or data downloads merely because a user opens a report. Optional, so users can backtest without an AI subscription.

### Later — options and operating deployment

Strategy Builder combined-premium/OI charts and the existing risk core can support future options and paper/live handoff once our instrument, cost, position and evidence contracts cover them. Voice, live resizing and kill-switch integration are separate operator-facing scopes. They should not delay the present native data → causal research → untouched evaluation path.

## Source pointers

- Exact compare: https://github.com/marketcalls/openalgo/compare/c6a431401872691f3d46e0bb840a3637e18b0591...d858c2384cbd6a9bdb09d0fb42c1a08b46c48c29
- Release 2.0.2.3: https://github.com/marketcalls/openalgo/blob/d858c2384cbd6a9bdb09d0fb42c1a08b46c48c29/docs/releases/version-2.0.2.3-released.md
- Release 2.0.2.4: https://github.com/marketcalls/openalgo/blob/d858c2384cbd6a9bdb09d0fb42c1a08b46c48c29/docs/releases/version-2.0.2.4-released.md
- Release 2.0.2.5: https://github.com/marketcalls/openalgo/blob/d858c2384cbd6a9bdb09d0fb42c1a08b46c48c29/docs/releases/version-2.0.2.5-released.md
- Shared chart source: https://github.com/marketcalls/openalgo/blob/d858c2384cbd6a9bdb09d0fb42c1a08b46c48c29/frontend/src/components/chart/OpenAlgoChart.tsx
- Chart indicator catalog: https://github.com/marketcalls/openalgo/blob/d858c2384cbd6a9bdb09d0fb42c1a08b46c48c29/docs/prompt/indicators/chart-indicators.md
- Python indicator registry: https://github.com/marketcalls/openalgo/blob/d858c2384cbd6a9bdb09d0fb42c1a08b46c48c29/services/agent/indicators/registry.py

Release 2.0.2.5 page was also fetched from the official GitHub site during this audit. Local fetched commit contents were used for the detailed complete-range comparison and all source-level findings.
