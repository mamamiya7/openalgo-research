# OpenAlgo Documentation Map

The entry point for humans and AI agents. This file is a **map, not a copy** —
it points at the canonical docs that already live under `docs/`. Edit a source
doc once; everything that reads through this map sees the change immediately.

**How to use (progressive disclosure):** read this map → open the one area you
need → drill into the specific file. Don't load everything at once.

---

## Using OpenAlgo (product, API, SDK)

| Read this when you need… | Entry point |
|---|---|
| The REST API (`/api/v1/`) — orders, market data, options, account, streaming | [api/README.md](api/README.md) |
| The Python SDK — install, client, data/order/account calls | [<prompt/openalgo python sdk.md>](<prompt/openalgo python sdk.md>) |
| Symbol format across exchanges (equity/futures/options) | [prompt/symbol-format.md](prompt/symbol-format.md) |
| Order constants (exchange / product / price-type / action codes) | [prompt/order-constants.md](prompt/order-constants.md) |
| Contract lot sizes | [prompt/LotSize.md](prompt/LotSize.md) |
| WebSocket subscription & message format | [prompt/websockets-format.md](prompt/websockets-format.md) · [prompt/websockets-verbose-control.md](prompt/websockets-verbose-control.md) |
| Service-layer functions & Flow JSON import | [prompt/services_documentation.md](prompt/services_documentation.md) · [prompt/flow-import-format.md](prompt/flow-import-format.md) |
| Strategy module & risk engine (multi-leg options, signal mode, RMS) | [prompt/strategy_rms_documentation.md](prompt/strategy_rms_documentation.md) · [api/strategy-services/](api/strategy-services/) · [prd/strategy-module-rms.md](prd/strategy-module-rms.md) · [bdd/strategy_module_rms.feature](bdd/strategy_module_rms.feature) |
| Technical indicators (`ta` library) | [<prompt/indicators/openalgo indicators - introduction.md>](<prompt/indicators/openalgo indicators - introduction.md>) |
| The charting terminal at `/trading`, its order dock and its shortcuts | [userguide/32-charting-terminal](userguide/32-charting-terminal/README.md) |
| Writing your own chart indicators for `/trading` | [custom-indicators.md](custom-indicators.md) |
| Step-by-step user guide (setup → first order → integrations) | [userguide/README.md](userguide/README.md) |
| MCP tool reference (Claude Desktop / Cursor / Windsurf) | [mcp-tool-reference.md](mcp-tool-reference.md) |
| Native Research roadmap: research intelligence foundations, broader methods and simple automatic journeys | [research/DELIVERY_PLAN.md](research/DELIVERY_PLAN.md#feature-development-order-after-the-human-audit) |
| Research implementation now: qualified package slices, remaining core sequence and next coding acceptance | [research/DELIVERY_PLAN.md](research/DELIVERY_PLAN.md#current-completion-against-the-eight-packages) · [next steps](research/DELIVERY_PLAN.md#remaining-implementation-order) |
| Scanner Research: dated CSV, historical execution, search, later tests and retained evidence | [research/STATUS.md](research/STATUS.md) · [research/DEVELOPMENT.md](research/DEVELOPMENT.md) |
| Search-seed stability and controlled false-recommendation calibration | [research/SEARCH_SEEDS_20261021.md](research/SEARCH_SEEDS_20261021.md) · [research/SEARCH_CALIBRATION_PROTOCOL.md](research/SEARCH_CALIBRATION_PROTOCOL.md) |
| Native synthetic full-procedure calibration and whole-procedure sampler checks | [research/NATIVE_CALIBRATION_20261021.md](research/NATIVE_CALIBRATION_20261021.md) |

| Portfolio-wide allocation and measured native runtime | [research/PORTFOLIO_ALLOCATION_20261021.md](research/PORTFOLIO_ALLOCATION_20261021.md) � [runtime](research/PORTFOLIO_RUNTIME_20261021.md) |

## Install, deploy & operate

| Topic | Entry point |
|---|---|
| OpenAlgo Research: start here | [Guided installation and troubleshooting](research/RUNTIME.md#easy-local-installation-recommended) · [first-time setup](userguide/05-first-time-setup/README.md) |
| Upstream OpenAlgo Ubuntu server reference | [installation-guidelines/getting-started/ubuntu-server-installation.md](installation-guidelines/getting-started/ubuntu-server-installation.md) |
| Docker | [docker/README.md](docker/README.md) |
| OpenAlgo Research distribution: guided setup, launcher, update and compatibility | [research/DISTRIBUTION.md](research/DISTRIBUTION.md) · [research/RUNTIME.md](research/RUNTIME.md) · [offline start guide](../START_HERE.html) |
| Upgrade / SMTP / TOTP / forgot-password | https://docs.openalgo.in/installation-guidelines/getting-started/ |
| Broker integration (36 plugins) | [broker-integration-guide.md](broker-integration-guide.md) |
| Release notes & changelog | [releases/](releases/) · [CHANGELOG.md](CHANGELOG.md) |

## Feature surfaces

| Feature | Entry point |
|---|---|
| Native Scanner Research development | [research/BRIEF.md](research/BRIEF.md) · [research/STATUS.md](research/STATUS.md) · [research/SYSTEM_MAP.md](research/SYSTEM_MAP.md) · [research/CONNECTORS.md](research/CONNECTORS.md) · [research/JOURNEYS.md](research/JOURNEYS.md) · [research/EXECUTION_STATUS.md](research/EXECUTION_STATUS.md) |
| Complete trader journeys, study library, navigation and asset-aware design | [research/PRODUCT_JOURNEY_MAP.md](research/PRODUCT_JOURNEY_MAP.md) · [research/ASSET_PROFILES.md](research/ASSET_PROFILES.md) · [visual map](research/trader-journey-map.html) |
| Integrated Research delivery plan: reconciled whole-product backlog, all 34 journeys, dependencies, assets and release acceptance | [research/DELIVERY_PLAN.md](research/DELIVERY_PLAN.md) |
| Research workflow refinement: import, automatic progress, actionable recovery, decisive findings, saved setup reuse and trader inspection | [research/WORKFLOW_REFINEMENT_20261011.md](research/WORKFLOW_REFINEMENT_20261011.md) |
| OpenAlgo 2.0.2.5 upgrade, chart/indicator reuse and upstream feature inventory | [research/UPSTREAM_UPDATE_20260916.md](research/UPSTREAM_UPDATE_20260916.md) |
| Saved trade charts: frozen native candles, entry/exit markers, visual indicators and bounded bar replay | [research/SAVED_TRADE_CHARTS.md](research/SAVED_TRADE_CHARTS.md) |
| Proposed one-click Optimize: automatic trading-rule/condition research, causal indicators, validation, benchmarks and findings | [research/AUTOMATED_RESEARCH_PLAN.md](research/AUTOMATED_RESEARCH_PLAN.md) |
| Native research timing admission: actual CSV timestamps, daily/minute intent and prerequisites for future clock search | [research/TIMING_ADMISSION_20261011.md](research/TIMING_ADMISSION_20261011.md) |
| Shared native timing evidence: bounded clock choices, one Historify union and a fixed missing-price cohort | [research/TIMING_UNION_20261011.md](research/TIMING_UNION_20261011.md) |
| Automatic trade timing: shared evidence, joint search, native recovery and exact saved-clock replay | [research/AUTOMATIC_TIMING_20261011.md](research/AUTOMATIC_TIMING_20261011.md) |
| Research download batching, Historify reuse and bounded evidence publication | [research/DOWNLOAD_PERFORMANCE.md](research/DOWNLOAD_PERFORMANCE.md) |
| Optimization trial metrics, configurable table columns and settings dialogs | [research/TRIAL_RESULTS.md](research/TRIAL_RESULTS.md) |
| Continuous report, saved preferences, month/drawdown investigation and evaluation-basis receipts; Optuna study, candidate and comparison/validation plan | [research/REPORT_EXPERIENCE_PLAN.md](research/REPORT_EXPERIENCE_PLAN.md) |
| Chartink extension to saved research: full journey, names, automatic fields, required choices, engines and results | [research/CHARTINK_JOURNEY.md](research/CHARTINK_JOURNEY.md) |
| Chrome extension logo, public release steps, store copy and privacy | [research/CHROME_EXTENSION_PUBLISHING.md](research/CHROME_EXTENSION_PUBLISHING.md) · [research/CHARTINK_STORE_LISTING.md](research/CHARTINK_STORE_LISTING.md) · [research/CHARTINK_PRIVACY.md](research/CHARTINK_PRIVACY.md) |
| Agent (`/agent`) | [design/55-agent/README.md](design/55-agent/README.md) |
| Scalping Terminal (`/scalping`) | [scalping/PRD.md](scalping/PRD.md) |
| Scanner architecture | [scanner-architecture.md](scanner-architecture.md) |
| WhatsApp alerts | [whatsapp.md](whatsapp.md) |
| Telegram chart rendering | [telegram-chart-rendering.md](telegram-chart-rendering.md) |
| Health monitoring | [HEALTH_MONITORING_IMPLEMENTATION.md](HEALTH_MONITORING_IMPLEMENTATION.md) · [HEALTH_MONITOR_REACT_FRONTEND.md](HEALTH_MONITOR_REACT_FRONTEND.md) |

## Architecture, design & specs (contributors)

| Topic | Entry point |
|---|---|
| First-time contributor setup (devsprint prep) | [devsprint/README.md](devsprint/README.md) |
| System design (frontend, backend, DB, UI) | [design/README.md](design/README.md) |
| Product requirements — Flow, Python strategies, Strategy module & RMS, Sandbox, Historify, MCP, event bus, websocket proxy | [prd/README.md](prd/README.md) · [prd/PRD.md](prd/PRD.md) |
| BDD feature specs (Gherkin `.feature`) | [bdd/README.md](bdd/README.md) |
| WebSocket architecture & quote feed | [websocket-architecture.md](websocket-architecture.md) · [websocket-quote-feed.md](websocket-quote-feed.md) |
| Security audits | [audit/README.md](audit/README.md) |
| CI/CD | [prd/ci-cd.md](prd/ci-cd.md) |
| Benchmarks | [benchmarks/](benchmarks/) |
| Migration plans | [migration/](migration/) |
| Implementation plans | [plans/](plans/) |
| Testing guides | [test/](test/) |
| XTS API | [xtsapi.md](xtsapi.md) |

---

## Governance

- User responsibilities & risk ownership: https://docs.openalgo.in/responsibilities
- Repository: https://github.com/marketcalls/openalgo · Docs: https://docs.openalgo.in

- [Native research refinement implementation](research/NATIVE_REFINEMENT_20261007.md): current chart and joint-search contracts and remaining delivery gates.
- [Automatic trailing selection](research/AUTOMATIC_TRAILING_20261011.md): versioned on/off search, native evaluation, saved-state reuse and acceptance gates.
- [Expanding research validation](research/WALK_FORWARD_20261020.md): development v8 chronology, independent fits, native accounts, recovery and qualification scope.
- [Method admission foundation](research/METHOD_ADMISSION_20261020.md): typed pinned method definitions, on-demand saved coverage, qualification and the next numerical expansion.
- [Core method expansion](research/METHOD_EXPANSION_20261021.md): v9 momentum domains, mandatory probes, bounded search, native recovery and preserved legacy identities.
- [Turnover and recommendation evidence](research/TURNOVER_RECOMMENDATION_20261021.md): v12 fixed daily buy budgets, comparable opportunities, paired uncertainty and actual neighbouring-account gates.
- [Saved alternatives and volume admission](research/TRADEOFFS_VOLUME_20261021.md): read-only development trade-offs, exact settings inspection and the qualified-data boundary required before capacity limits.
- [Symbol and date sensitivity](research/SYMBOL_DATE_SENSITIVITY_20261021.md): v14 predeclared native subset accounts, saved support facts, conservative recommendation fallback and recovery.
- [Volume provenance and portfolio constraints](research/PORTFOLIO_CAPACITY_20261021.md): v13 native volume reuse, modeled participation budgets, causal correlation-aware allocation and saved recommendation audits.
