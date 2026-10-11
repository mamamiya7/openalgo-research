# OpenAlgo host update — 11 October 2026

This development increment imports upstream main at
`12e1114657981a3e50dde8c8ce04eb778ae1cf2f` (9 October, 20:52 IST),
following the 7 October baseline `ee26e4e24caeeb68a27a5417bf3744bc0c5e1964`.
The interval contains 14 commits, including three generated frontend builds,
and changes 99 source, dependency, documentation and test paths. The combined
Research frontend is rebuilt from source rather than replaced with upstream's
host-only assets.

The host version is now **2.0.2.7**, released on 8 October. The Research preview
version and its publication status are separate. This source update alone does
not confirm local activation or a public release; see [STATUS.md](STATUS.md).

[Upstream comparison](https://github.com/marketcalls/openalgo/compare/ee26e4e24caeeb68a27a5417bf3744bc0c5e1964...12e1114657981a3e50dde8c8ce04eb778ae1cf2f)
· [Official release](https://github.com/marketcalls/openalgo/releases/tag/openalgo-gthread-web-server).

## What changed since our last import

| Native area | Imported change | Research implication |
| --- | --- | --- |
| Fivepaisa history | Excludes candles outside the requested date window, including a broker's latest-candle fallback for an empty range | A requested gap cannot be satisfied by an unrelated date |
| Flattrade history | Rejects missing/non-finite OHLC, repairs BSE index ranges, uses the index exchange for today's quote, excludes pre-open bars and floors negative intraday volume | Newly acquired candles follow the corrected native broker adapter; no new downloader |
| Master contracts | Firstock, Flattrade and Tradejini validate before replacing symbols; Flattrade/Fivepaisa remove stale or duplicate rows and normalize instrument types | Failed native refreshes retain usable existing symbols |
| Groww | Aligns history, authentication, positions and other calls with the official API; separate history pacing and retry handling | Research continues through native History/Historify and respects broker pacing |
| Zerodha | Maps pending order statuses as open instead of inheriting a previous order's final status | Host correctness; Research has no order capability |
| Agent | Repairs simultaneous tool arguments, validates synonyms, supports daily-derived weekly/monthly views, shares OpenScript storage and reads native authoring skills | Optional future explanations and selected-setup study previews can reuse native authoring seams after parity checks |
| Dependencies | Werkzeug 3.1.9, multidict 6.9.1, frontend security overrides and CodeMirror lock refresh | Research calculation pins and custom dependency constraints are retained |

The charts and OpenScript packages **remain 2.6.0 and 0.8.1**; those capabilities
were already imported on 7 October. There are no new database, upgrade,
Historify schema or shared-history API changes in this interval. The general
2.0.2.7 release migration instruction refers to the Historify sequence migration
already present in the previous baseline; fresh installations still follow the
ordinary migration flow.

The new agent MCP client ships with **no external server registered**. Upstream
removed the NSE MCP endpoints after repeated timeouts. This is not a new
historical-price source for Research. Broker data continues to flow through
OpenAlgo's native History/Historify services. Neither agent trading permissions
nor gthread are enabled by this update.

## Numerical and saved-evidence continuity

Fresh Flattrade candles can intentionally differ: a range that did not contain
its recorded open/close is widened, a negative volume becomes zero, and invalid
rows or pre-open bars are excluded. Fivepaisa drops rows outside the requested
window. Tests exercise these corrections through stand-in broker responses.
Existing frozen Research inputs, reports and exact replays are not rewritten.

OpenAlgo SDK 2.0.5, VectorBT 0.28.5, Optuna 5.0.0, NumPy 2.4.4 and pandas 2.3.3
remain unchanged. Research's Plotly dependency and frontend version constraints
are preserved during the upstream dependency merge.

## Scoped qualification

- 849 backend checks pass; one symlink case is skipped because this Windows
  environment does not permit the test to create a symlink.
  These cover agent tools and transport, MCP failure/cache handling, native
  skills, OpenScript storage/routes/runner, Groww and Zerodha corrections and
  the previous broker-history contracts.
- Eight additional broker-history checks pass for the new Flattrade/Fivepaisa
  behavior, without contacting a broker.
- 23 native agent/MCP frontend checks pass across two suites.
- The combined Python lock validates and installs in the isolated environment;
  the combined npm lock installs successfully. The extra history test passes
  Ruff formatting/lint checks.

Resource ownership was reviewed statically: shared HTTP clients with existing
pool ownership, bounded MCP/skill/result caches, registry-bounded MCP client
state, the shared OpenScript keyed lock and staged-file cleanup on success and
failure, and broker transaction commit/rollback paths. No new calculation worker,
per-request executor, price database or long-lived stream is introduced. This
is a static audit plus controlled regression coverage, not a production soak
test. Native UI and combined-app activation are qualified separately in the
current delivery checkpoint.
