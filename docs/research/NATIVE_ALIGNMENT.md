# Native backtesting data alignment

Installed implementation and comparison with native OpenAlgo, 6 September 2026.
The initial read-only audit found the differences recorded below. The subsequent
authorized correction implements `openalgo-native-history-v1` for new native uploads.

## Existing feature contracts

| Feature | Resolution and selection | Source and storage |
| --- | --- | --- |
| Portfolio Backtester | Daily closes, fixed by the calculation | UI defaults to Broker API; the user can choose Historify. Shared `portfolio.data.load_prices` reads the chosen source, without fallback or persisting API responses. |
| SIP Backtester | Daily closes; contribution frequency does not change candle interval | Same loader and source choices; UI defaults to Broker API. |
| Portfolio Analyzer | Daily closes for historical analysis of current holdings | Current holdings come from the broker; historical source defaults to Historify and can be changed to Broker API. |
| Straddle PnL | Caller-selected intraday interval, default one minute | Shared broker history for the underlying and option legs; no Historify cache/write path. |
| Historify | User-selected daily or one-minute storage interval | Manual or scheduled native history downloads explicitly persist into `market_data` and update the catalog. |
| Python/VectorBT examples | Interval and source explicitly configured by the script | OpenAlgo's history API/SDK; no general automatic CSV inference. |

Analyzer Mode is simulated order execution using live prices. Strategy Builder
uses option chains and hypothetical payoff calculations. Neither is a historical
scanner CSV backtester.

The feature supplies symbol, exchange, interval and date range. API-source history
resolves the existing authenticated broker and its normal adapter; database-source
history reads Historify. There is no universal cache-then-download behavior in all
native backtesters. The requested automatic Scanner Research workflow adds that
coordination over the shared services.

## Differences found before the correction

The earlier implementation shared the broker and database but added these differences:

1. Research's `evidence_mode` dispatch sends Fyers daily requests through the
   separate `get_history_evidence` implementation. Native Portfolio/SIP/Historify
   use ordinary `get_history`. Endpoint parameters match; processing differs.
2. Research requires pinned public NSE prices, daily identities and OHLC matches.
   Native Portfolio/SIP validate usable timestamps, positive finite closes and
   sufficient common sessions, and warn about suspicious large price moves;
   they do not require public-file price agreement. Of the latest 65 unresolved
   EOD dates, 41 have broker prices stored but rejected by Research's reference
   checks, while 24 were not returned.
3. Research applies additional public corporate-action factors. Native Portfolio
   consumes broker/stored prices as supplied. Merely removing a comparison while
   retaining that extra adjustment can double-adjust prices.
4. New Research daily rows are normalized to IST midnight, while native Historify
   preserves adapter timestamps. Different keys for the same trading date risk
   duplicate rows on native redownload and timezone-dependent dates in consumers.
5. Research blocks source publication for any required missing price. Native
   Portfolio takes common observed daily dates. Copying that date intersection
   into the scanner would change next-session entry, holding and stop semantics.

## Required integration contract

Keep the automatic CSV/settings planner: dated multiday scans use daily OHLC;
timestamps or explicit intraday settings require minute candles. It should supply
only the required symbols, exchange, interval and holding-date windows to native
data services. Use the ordinary active-broker history adapter and the same native
Historify database, preserving returned prices and timestamps. Retain immutable
research snapshots separately for reproducible calculations.

A new native-price policy must remove mandatory public-price matching and extra
public adjustment from the normal broker path together, without claiming
independent verification that OpenAlgo does not provide. Use the native market
calendar for required-session planning. Preserve finite/consistent OHLC checks,
scope limits, broker errors and unresolved trade outcomes for truly missing
prices. Do not fabricate bars or silently remove expected trading sessions.
Existing saved public/reference-qualified reports must keep their recorded
identities and policies. Previously normalized daily timestamps require a
verified, reversible reconciliation before changing keys in the owner's store.

The correction is implemented in `research_native_calendar.py` and
`research_native_prices.py`, selected by the new source's recorded native policy.
New native preparation uses the ordinary adapter for every broker, native calendar
sessions/hours, matching-interval cache first and exact native write/readback. No
public-file import, comparison or additional corporate-action adjustment occurs.
Successful requests with missing candles publish partial coverage and preserve
the scanner's pending-trade semantics. Failed requests remain resumable. Minute
holes in the same date share one history request, and already stored candles are
not overwritten by a broader broker response. Prior reports and legacy recovery
paths retain their original policies.

The owner's 1,976 previously normalized daily timestamps were matched against all
original broker observations, then transactionally reconciled to those timestamps.
Exact before/after row records are retained outside Git; all OHLC/volume values and
all other interval rows were verified unchanged. Native catalog bounds were updated.

Verification: the full Windows suite passed 456 tests with three link-permission
skips before the final additional acquisition tests. The final native calendar
suite passes 24 tests, acquisition suite 21 tests and daily/minute HTTP integration
suites 10 tests. Both workspace and existing-main frontend builds pass. Native
calendar resource tests show ten matching connection checkouts/checkins and session
removal; other changed resource paths were reviewed statically for context-managed
files/connections and bounded price, receipt and recovery journals. No new Linux
verification or real second-broker account test was performed in this correction.

The historical installed preparation and backtest both completed with the original
361-signal dated CSV and settings. Scope stays 2,000 daily candles; all 1,976
stored prices are admitted and equal the native database exactly. Fyers returned
no candles for the remaining 24 requested observations. The result records 200
closed, 149 skipped and 12 pending signals (four funded positions/eight without
entry); pending outcomes can also reflect the frozen end of history. Its immutable
result bundle SHA-256 and job identities are retained in private verification receipts.
The existing-account Chrome screen is used; no isolated app, new credentials or
second price database was introduced. The final UI suite passes 41 tests.

## Code evidence

- `portfolio/data.py`: `load_prices`, `_closes_from_duckdb`, `_assemble`, `split_artifacts`.
- `services/portfolio_service.py`: backtest and current-holdings analysis.
- `services/sip_service.py`: daily loader and contribution calculations.
- `frontend/src/pages/PortfolioBacktester.tsx`, `SipBacktester.tsx`, `PortfolioAnalyzer.tsx`: source selection/defaults.
- `services/custom_straddle_service.py`, `frontend/src/pages/CustomStraddle.tsx`: intraday simulation.
- `services/history_service.py`: source and broker adapter routing.
- `services/historify_service.py`: native download and persistence.
- `services/research_sources.py`, `research_acquisition.py`, `research_historify.py`: additional research preparation/qualification.
