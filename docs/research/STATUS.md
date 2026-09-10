# OpenAlgo Research status

Updated: 10 September 2026. Published preview: **`0.1.0-preview.4`**, targeting OpenAlgo 2.0.2.2.

## Current development increment

The native Research library, server-saved experiment drafts, setup versions,
revision conflict recovery, archive/restore and result-to-setup/study/replay
transitions are implemented in the development worktree. The integrated browser
journey passed with actual VectorBT/Optuna calculations and controlled native
Historify prices. The increment was installed into the user's existing instance
for native testing on 10 September; it is included in the development source and
has not yet been packaged as a new versioned release.
Native testing exposed a price-download deadline failure before optimization.
Automatic download continuation and compact animated stage/counter reporting
are now implemented, specified in [run progress](RUN_PROGRESS.md). The progress
increment passed controlled browser acceptance and was installed into the user's
normal app on 10 September. Existing configuration and saved runs were preserved;
no active job was interrupted. The latest packaged release remains `0.1.0-preview.4`.
See the [implementation checkpoint](EXECUTION_STATUS.md)
for working journeys, remaining M1/M2 work and verification boundaries.

Frontend verification for the library increment: **41 tests passed** across the existing
portfolio journey checks, draft persistence/recovery and new library navigation;
TypeScript, scoped Biome and a production build passed. Browser checks also
covered light/dark layouts, keyboard focus restoration, deletion cancellation,
notes search and old-report access. Automated accessibility checks reported no
WCAG A/AA violations on the checked library, setup and study surfaces; this is
bounded coverage, not a whole-application accessibility certification.

The subsequent progress increment passed **62 frontend tests**, native acquisition
and Optuna regressions, controlled price preparation, a cache-only repeat,
selected exact replay and restart checks. Detailed counts and scope are recorded
in the [implementation checkpoint](EXECUTION_STATUS.md).

Backend verification: **52 library/storage tests passed**, including real
Optuna/VectorBT calculation, selected replay, concurrent edit/submission races,
ownership, populated migration, retention and backup/restore. A Windows
maintenance failure with long destination paths was reproduced and corrected;
deep backup/restore, failure cleanup and containment have regression coverage.
The 100-cycle handle check remained within its bound. Ruff and whitespace checks
passed. A final native-harness restart preserved authentication, drafts, versions,
study/replay links and the original export bytes.

## Published preview baseline

The native **Backtest & Optimize** workflow is implemented: CSV inputs, automatic
native price preparation, shared-account VectorBT backtests, Optuna searches,
later-period checks and exact saved replay. An optional NautilusTrader Linux/WSL
runtime uses the same workflow with its declared execution rules.

This is an installable preview of the declared scope. Broader broker, Docker and
cross-version upgrade acceptance remains open. [Release notes](releases/0.1.0-preview.4.md),
[installation](DISTRIBUTION.md) and the [system map](SYSTEM_MAP.md) describe the
public distribution.

## What is delivered

| Area | Implemented scope |
| --- | --- |
| Portfolio | One to eight long NSE cash-equity CSV signal strategies, separate rules and allocations, one shared engine account. |
| Inputs | Upload CSV or reuse account-scoped saved signals; selecting a saved source starts no download. Daily and timed CSV examples are available in a collapsed help section. |
| Data | Automatic daily/one-minute selection, finest required interval across the account, full search-window planning, Historify reuse and missing-only native broker acquisition. |
| VectorBT | Real 0.28.5 portfolio calculations, daily/intraday/multiday execution, native fills, cash and fees, with reconciled strategy contributions. |
| NautilusTrader | Real 1.231.0 Linux BacktestEngine, independent strategy/position identities, shared INR cash and daily/minute inputs under a recorded OHLC model. |
| Optimization | Real Optuna 5.0.0 TPE or grid over selected settings and allocations, bounded trials, objectives and version-bound recovery. |
| Coverage | A fixed eligible signal cohort before trials; explicit price/instrument exclusions and no invented prices. |
| Later period | Earlier-only selection and a separate later simulation with fresh capital; positions cannot cross the boundary. |
| Results | Combined account, strategy contributions, Trades/Trials/Settings, saved runs and exact evidence export. |
| Replay | Recorded settings and frozen inputs; no new download or optimization. |
| MCP | Twelve account-scoped research tools through existing OpenAlgo authentication, explicit remote research scopes and bounded responses. |
| Handoff | Selected strategy-definition export with execution disabled. |

VectorBT and Optuna TPE are defaults. Nautilus is available in advanced settings
when its runtime is installed. Unsupported settings are not silently changed.
Older scanner reports retain their original evidence and execution policies.

Preview.4 also corrects recognized native NSE calendar seed errors with an
idempotent migration that preserves custom entries. Calendar planning follows
actual entry and holding requirements, including special sessions and optimizer
ranges. Worker shutdown preserves resumable progress. The ordinary Fyers history
adapter accepts its explicit successful no-data response and propagates exhausted
or malformed requests instead of returning a partial success.

## Verification at this preview

| Check | Result and scope |
| --- | --- |
| Final Windows research suite | **774 passed, 42 skipped**, with one existing Pydantic warning. Platform/optional checks remain separate from this run. |
| Additional targeted checks | Native calendar: **30 passed**. Fyers/history/acquisition targets: **105 passed**. These overlap the full suite and are not additive totals. |
| Native interface | **29 focused journey checks**, TypeScript, scoped Biome and production build; desktop/mobile keyboard interaction and CSV example downloads exercised. |
| Fresh Windows preview.4 ZIP | A fresh extracted directory and new locked environment passed setup/login, built assets, two-strategy real VectorBT calculation, real Optuna trials, exact selected replay, restart and export preservation. **40 artifact checks passed**. Graceful shutdown left no owned processes, listening test ports or worker lease. |
| Windows installation boundary | The fresh-directory check used an existing Windows host and cached packages with controlled Historify candles. It was not a clean OS image or a real broker login. |
| Linux installation | Earlier preview installation with new locked core/Nautilus environments passed setup, real HTTP/worker calculations, exact replay and an idempotent populated reinstall. Account, configuration and exported reports survived reinstall; owned processes and ports were cleaned up. |
| Nautilus boundary | **29 real native Linux checks** and **10 instrument-metadata checks**, plus cancellation, malformed response and repeated-run cleanup coverage. Real calculations through the Windows/WSL bridge completed. |
| Real broker path | Fyers daily preparation and acquisition of an absent minute window were exercised in an existing OpenAlgo installation. Frozen OHLC matched its native Historify archive; replay and subsequent archive reuse were verified. |

The daily archive-reuse check still made requests for uncovered windows that
returned no usable new candles. Zero newly stored candles does not imply zero
broker requests. Exact saved replay performs no new acquisition.

The final Windows installation check preceded publication preparation and frontend
dependency patches. Its calculation code and Python dependency locks remain the
tested preview.4 baseline; the frontend is rebuilt and checked after its updates.
Release assets contain a file-hash manifest and checksums identifying their final
contents. Historical checks are recorded in [VERIFICATION.md](VERIFICATION.md).
Private inputs, accounts, databases and machine-specific receipts are excluded
from Git and release inventories.

## Data and execution rules

Historify remains the mutable native price archive. Missing required coverage goes
through the connected broker's OpenAlgo history service, is saved and read back
there, then is frozen for calculation. Date-only signals with daily execution
rules remain daily. Timestamped signals or explicit intraday rules select
one-minute data. Stops, targets and trailing rules alone do not force minute data.

Price and instrument eligibility consider each strategy's maximum allowed holding
window before optimization. A successful absent-price response or unavailable
master symbol can record an exclusion. Authentication, rate-limit, server and
unknown request failures remain resumable acquisition errors.

VectorBT entry-bar protection and Nautilus's modelled OHLC matching are distinct
policies. OHLC-derived events are not observed ticks. Nautilus checks raw prices
against supplied native instrument metadata without rounding or guessing historic
tick sizes. Shared-account results and strategy attribution reconcile to the
selected engine's results. See [CONNECTORS.md](CONNECTORS.md).

## Remaining boundaries

- Calendar admission covers 2025–2026 and rejects required special sessions with
  unconfirmed hours. Security-specific auction eligibility, closing-auction
  behavior and exchange matching chronology are outside this model.
- Real broker acceptance covers the recorded Fyers daily/minute cases. Other
  adapters have controlled checks, not universal live-account certification.
- Public CI built the Docker image and probed both locked Python environments.
  Full container setup, application startup, restart and restore acceptance remain
  separate from these build and dependency checks.
- The targeted NSE calendar migration and populated same-version reinstall are
  verified. Broad cross-version update, restore and rollback coverage, clean OS
  installation and unaided consumer acceptance remain open.

The connector does not import arbitrary engine programs or implement shorts,
derivatives, leverage, shared margin, corporate-action cashflows or observed-tick
replay. Nautilus additionally excludes trailing stops and configured nonzero
slippage. Optuna Dashboard and a general rolling walk-forward builder are outside
this preview. Research export does not activate paper/live strategies.
