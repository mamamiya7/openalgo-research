# OpenAlgo host refresh — 22 October 2026

Updated the native host from upstream `12e1114657981a3e50dde8c8ce04eb778ae1cf2f`
to `f3e7de829d7c93515a3e041bd2a637546c62a1d7`: 42 commits including generated
frontend builds. Platform **2.0.2.7 →2.0.2.9**; charts **2.6.0 →2.6.7**.
Research is **0.1.0-preview.19**, a separate distribution version. Python SDK
2.0.5 and OpenScript 0.8.1 are unchanged.

[Official 2.0.2.9 release](https://github.com/marketcalls/openalgo/releases/tag/openalgo-custom-timeframe-charts)
is dated 17 October. This reviewed main snapshot also contains subsequent official
Strike Profile and dependency updates through 22 October; it is not described as
the release tag alone. Native release notes and all upstream contributor credits
are retained in docs/releases and docs/CHANGELOG.md.

## Native changes and Research integration

| Area | Update and effect |
|---|---|
| Charts | Custom intervals/favorites, daily study history on intraday charts, chart controls, fullscreen, Renko/Point & Figure and navigation improvements. Rebuild combined assets; saved Research charts keep frozen data/replay rather than switching to a live provider. |
| Historify | Custom daily multiples and continuous multi-week/month/quarter/year anchors. Ordinary D/1m storage reads are unchanged. |
| Research chart qualification | Existing chart wrapper, saved markers, replay, indicator feed and values/journey tests pass against charts 2.6.7. Higher-timeframe native studies are available on native charts; automatic research methods are not expanded. |
| Broker/native tools | Rupeezy plugin, resilient Kotak/Shoonya master refreshes, Fyers stream precision, Strike Profile, strategy editor controls and crypto scheduling. Historical Fyers adapter/history service/calendar are unchanged. |
| Exchange metadata | Apply upgrade/migrate_qty_freeze_oct_2026.py for NSE index freeze quantities, preserving administrator overrides. No Research or Historify schema migration. |
| Dependencies | certifi 2026.10.19, MCP 1.30.0, Pillow 13.0.0, aiohttp 3.14.5 and frontend security updates. The merged Python project explicitly pins aiohttp to the same patched version as both requirements files; retaining the previous transitive lock would otherwise keep 3.14.3. |

## Correctness correction found during qualification

The upstream multi-period aggregate used timezone-aware DuckDB truncation, which
returned different chart date-label epochs on Kolkata hosts. The shifted IST
civil clock is now made explicitly UTC-naive before truncation. Existing expected
values were retained; UTC, Kolkata and New York each pass. Native D and 1m values
retain their exact epochs/OHLCV. This intentionally fixes new aggregate labels,
not stored prices or saved Research evidence.

The reviewed Fyers historical adapter digest and volume units are unchanged.
VectorBT 0.28.5, Optuna 5.0.0, NumPy 2.4.4, pandas 2.3.3 and financial versions
are retained. Saved reports, sources and checkpoints are not rewritten.

## Focused qualification

- 172 backend checks cover Fyers precision, quantity migration, native archive
  batching/scope, daily/minute history evidence, volume acquisition/provenance,
  distribution contracts, setup and requirements parity.
- 21 timezone/custom-timeframe cases pass (including native daily/minute guards):
  **193 unique backend checks** across the focused qualification.
- **99 frontend checks /9 suites** cover the native chart wrapper, saved trade
  chart/feed/indicators/values/journey, navigation and Historify intervals.
- TypeScript and combined production build pass with charts 2.6.7 and private
  Node 24.22.1. System Node is left unchanged; consumers need no Node installation.
- Resource review: existing context-managed native DB reads and bounded caches;
  migration sessions and engines close on all paths. No new Research resources.

Tests use isolated databases and synthetic broker traffic; no live orders or
broker download is claimed. These scoped checks are not a complete every-broker,
every-tool or Linux/Docker installation acceptance. No full optimisation is rerun
for unrelated host/dependency changes. Public package is rebuilt from merged
source, not copied from a credentialed live installation.

## Windows bootstrap correction

Actual installation found that Windows PowerShell inherited a module path in
which Get-FileHash was unavailable after downloading uv. Bootstrap now computes
the same pinned SHA-256 with disposable .NET streams and extracts the verified
ZIP with .NET, removing the optional hash/archive module dependency. The
preliminary preview.18 source candidate is superseded by preview.19.

## Upgrade operation

Update from the full preview.19 release ZIP. Setup syncs the frozen dependencies,
checks compatibility and runs the index freeze-limit migration before restart.
It is idempotent and preserves custom admin values. Existing data paths and
private .env remain in place. A source build requires patched Node matching
frontend/package.json; release ZIPs include the built UI.
