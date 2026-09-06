# Scanner Research in OpenAlgo

Status: project setup approved and completed on 2026-09-06; native feature implementation is authorized to begin. This is the current direction. Earlier ideas to embed the old app or retain a separate FastAPI web backend have been superseded.

## Product

Build a native OpenAlgo feature that helps a user assess dated stock-scanner signals through historical simulation, parameter exploration, later-period testing and retained evidence. The user should understand the input, price coverage, execution assumptions, individual trade outcomes, portfolio numbers and justified next action.

Start with end-of-day NSE-equity scanner signals supplied as CSV. Intraday simulation and live execution are separate future work. OpenAlgo's existing Portfolio Backtester, SIP tools and Analyzer Mode remain complementary surfaces; their allocation simulation and current-price sandbox do not replace historical scanner-trade evaluation.

## Architecture

- React/TypeScript screens using OpenAlgo's components, navigation, API client and authentication conventions.
- Flask / Flask-RESTX routes and service modules inside this project. No Streamlit or retained FastAPI server in the feature.
- A framework-independent Python research package for normalized signals, historical execution, parameter search, evaluation and reports. Selectively reuse tested algorithms when they fit; the old application architecture and database layout are not dependencies.
- A bounded calculation worker process shipped with OpenAlgo. Persist submission/status, return a job identifier promptly, support progress and cancellation, and publish completed results atomically. Define restart/interruption behavior explicitly. Do not run expensive calculations inside OpenAlgo's broker-facing request loop.
- Use OpenAlgo's existing broker/history and symbol services. Historify owns the mutable broker-candle archive; calculations consume checked immutable snapshots. Preserve provider, exchange, instrument identity, interval and adjustment basis. Do not silently blend Yahoo-adjusted and broker series.
- Retain OpenAlgo operational databases. Add research metadata through its SQLAlchemy conventions, initially a feature-specific `research.db`, and manage larger immutable signal/price/result artifacts in an ignored research-data directory. The exact schema should follow implementation evidence and explicit migrations.
- Keep saved research review independent of a currently valid broker token. Authentication is required for protected research records; only new broker downloads require a valid broker session.
- Keep the feature patch modular so upstream OpenAlgo updates remain manageable. Cloud persistence and paid infrastructure require a separate rollout decision and restart/deploy/restore verification.

## First working milestone

Deliver a complete native journey: OpenAlgo Tools -> Scanner Research -> upload CSV -> inspect normalized signal receipt and price coverage -> choose one fixed setup -> submit historical simulation -> see progress -> review daily portfolio curve and explained ledger -> reopen the saved result and export its recorded evidence.

Start with isolated deterministic fixtures so feature development does not depend on a live broker session. The data boundary should support the existing Historify archive and expose missing data honestly. Do not make a demo fixture look like a real download. Establish one complete tested journey before expanding into Optimize and Research controls.

## Historical execution requirements

Signals are observations at the end of a session. Entry occurs at the following exchange session's open. Opening gaps through stops or targets fill at the observed open. When a daily bar reaches both levels and the opening price does not resolve their order, use the documented conservative stop-first assumption. Trailing changes take effect from a completed bar on the next bar. Holding duration uses exchange sessions.

Track cash, funded positions and pending outcomes. Missing intervening candles cannot prove that a stop was avoided. Opening exits may fund opening entries; later exits may not retroactively fund earlier orders. Apply configured costs/slippage consistently. Primary equity includes cash and marked open positions; label realized-only diagnostics separately. Verify the full policy against the old app's current product contract and representative fixtures before implementation. Explain any intentional correction.

Every saved result must retain its exact normalized signals, configuration, price snapshot, policy versions, coverage findings and result identity. Reopening or exporting a completed experiment must not silently redownload prices and recalculate the report.

## Following milestones

1. Search target/stop/hold/trigger/trailing settings with explicit budgets, bounded jobs and explained candidate comparisons.
2. Sensitivity analysis, fixed-setup later-period evaluation and earlier-only selection followed by later testing. Track prior exploration and avoid implying independent evidence from overlapping samples.
3. Expanded findings, historical evidence imports if useful, and verified broker-data acquisition and persistence rollout.

## Verification and delivery

Verify CSV error handling, data identity/coverage, accepted and pending trades, fill ordering, cash constraints, costs and daily metrics using deterministic datasets. Verify Flask authentication and direct result access, job lifecycle, cancellation/restart, immutable evidence and React navigation. Use the repo's actual test/build configuration and relevant OpenAlgo resource audit. Test both Windows development behavior and the production Linux/eventlet constraints; clearly distinguish unavailable checks from passes.

Ship a reviewable local result with concise evidence. Do not change the upstream OpenAlgo version just to mark feature progress, deploy the project, publish a remote repository, or connect live trading as part of this first milestone.

## Baseline

This independent checkout starts from the locally reviewed OpenAlgo main commit `c6a431401872691f3d46e0bb840a3637e18b0591` (project version 2.0.2.2). Upstream source: https://github.com/marketcalls/openalgo . No working-tree edits, broker credentials, live databases or environments were copied from the user's existing installation.
