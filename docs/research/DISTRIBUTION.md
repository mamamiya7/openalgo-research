# OpenAlgo Research: one installable distribution

The deliverable is one versioned OpenAlgo source bundle with a native **Backtest &
Optimize** screen. Users keep one OpenAlgo account, broker connection and Historify
price archive. VectorBT or NautilusTrader calculates trades and portfolio accounts;
Optuna searches the selected settings. Our layer coordinates those tools, prepares
prices, saves runs and presents their results.

This is a native distribution fork of [OpenAlgo](https://github.com/marketcalls/openalgo),
with explicit host integration hooks, a front end and a worker.
The [Research repository](https://github.com/mamamiya7/openalgo-research) and
[versioned preview release](https://github.com/mamamiya7/openalgo-research/releases/tag/research-v0.1.0-preview.4)
are the distribution locations. [Release notes](releases/0.1.0-preview.4.md) record
this preview's changes and verification boundaries.

## What is included

- One to eight dated or timestamped NSE cash-equity scanner CSV strategies, with
  separate rules and allocation caps against one shared starting-cash account.
- Fixed backtests through VectorBT by default, with NautilusTrader available when
  its separate Linux runtime is installed. Settings expose each engine's supported
  rules and preserve the user's choices when switching engines.
- Optuna optimization of selected strategy parameters and allocations, using TPE
  by default or a bounded grid. Fixed parameters stay fixed. Objectives are Return,
  Drawdown, or Balanced: return percentage minus maximum drawdown percentage.
- An optional later-period check: choose settings on the earlier 80% of distinct
  signal dates, then test those settings on the later period with fresh starting
  cash. Trades cannot cross the partition. At least five distinct dates are needed.
- Combined equity, cash, PnL, return, drawdown and trades; strategy contributions;
  separate Trades, Settings and Trials views; saved results, exports and exact
  frozen-input reruns. Earlier scanner runs retain their original evidence.

Upload the CSVs, set allocations and rules, then choose **Run backtest** or **Run
optimization**. The same job prepares prices and calculates the result. There is
no separate consumer price-preparation action or candle-interval selector.

The planner uses the CSV timestamps, holding rules and the full allowed optimizer
range. Date-only signals with ordinary daily rules use EOD candles; timestamps or
explicit intraday rules require one-minute candles. It reads the matching native
Historify coverage first, obtains missing required prices through OpenAlgo's
connected broker, saves them into the same archive and freezes the calculation
snapshot. Each engine and every optimization trial use that planned cohort.
Successfully unavailable signal windows are reported as exclusions; a failed broker
request is an acquisition error, not permission to substitute another price source.

Execution policies and supported scope are documented in
[CONNECTORS.md](CONNECTORS.md) and [NAUTILUS.md](NAUTILUS.md).

## Install and open

Download `openalgo-research-0.1.0-preview.4.zip` from the preview release, compare
its SHA-256 with the supplied `SHA256SUMS`, and extract it into its installation
directory. Use this versioned asset for its prebuilt interface; GitHub's automatic
source archives are source checkouts. For a fresh
installation, create `.env` from `.sample.env` and complete normal OpenAlgo
configuration. For an existing installation, preserve its `.env`, account data,
Historify archive, strategies and saved research evidence.

The native Windows and Linux core uses Python 3.12 with the locked research extra:

```sh
uv sync --frozen --extra research
uv run --no-sync python tools/research_check.py
```

Follow [RUNTIME.md](RUNTIME.md) to start the normal application and its research
worker; it includes the Windows PowerShell setup and two-terminal start/stop
sequence. Open the installation's usual address, normally `http://localhost:5000`,
log in and choose **Tools → Backtest & Optimize**. The route remains
`/scanner-research`. A release bundle contains its built front end; a source
checkout needs `npm ci` and `npm run build` in `frontend`.

VectorBT and Optuna run in the core environment on Windows and Linux. Nautilus
uses its own pinned Linux environment to keep its Arrow dependency separate from
OpenAlgo's. On a Windows-hosted checkout, open its directory under `/mnt/<drive>`
in the chosen WSL distribution, ensure Linux `uv` is available, and run:

```sh
uv run --no-project python tools/research_install_nautilus.py --wsl-distribution Ubuntu
```

Use the actual distribution name if it differs from `Ubuntu`. The installer copies
only `pyproject.toml` and `uv.lock` into a stable Linux cache, installs the frozen
runtime and checks its native engine before publishing the runtime configuration.
The application and private data remain on Windows. See [NAUTILUS.md](NAUTILUS.md)
for Linux installation and the optional `--runtime-dir` setting.

The supplied Docker Compose setup builds a Bookworm-based Linux image containing
both locked environments and starts the web app, proxy and research worker:

```sh
docker compose up --build -d
```

The Dockerfile and CI checks are implemented; a successful local Docker build or
fresh container setup has **not yet been verified** for this preview.

## Updates and recovery

Install a tested distribution release rather than upgrading individual engine
libraries in place. Stop the app and worker, back up source, `.env`, OpenAlgo data,
Historify, strategy files and the entire research directory, then apply the new
source and its locked dependencies. Keep the same data paths and, for Compose,
project and volume names. Never delete data volumes as part of an update.

After applying this release's source and dependencies, inspect and apply its
targeted NSE calendar correction with the services stopped:

```sh
uv run --no-sync python upgrade/migrate_research_nse_calendar.py --status
uv run --no-sync python upgrade/migrate_research_nse_calendar.py
```

This migration changes only recognized incorrect seed entries, preserves custom
calendar entries, and is safe to repeat. It does not rewrite frozen research
reports. The older wholesale calendar-reset migration is omitted from the update
sequence; do not use it to apply this correction. Fresh native startup initializes
the reviewed baseline automatically.

The Nautilus environment has its own `research/runtimes/nautilus/uv.lock`. Re-run
its installer when a release changes that lock; Windows core dependencies remain
separate. For an engine update that needs rollback, retain the previous runtime
and use `--runtime-dir` for the new one. Back up the runtime configuration with the
application data. Restore a matching source, dependency runtime and data backup
when rolling back a migration; do not recalculate saved evidence to imitate an
older version.

Maintainers should:

1. Integrate upstream OpenAlgo changes and update the relevant host hooks or
   adapter. Keep Research versioning separate from OpenAlgo's version.
2. Update intended versions, manifests and locks together. Run
   `tools/research_check.py` to verify structural host/package compatibility.
3. Run the Windows/Linux compatibility workflow: portfolio accounting, Optuna
   recovery, daily/minute planning, saved evidence, UI workflows and process
   lifecycle. Linux CI installs the locked Nautilus side runtime, tests native
   fills and exercises the real core-to-side calculation boundary.
4. Build with `uv run --no-sync python tools/research_release.py`. The source ZIP
   includes the built front end, dependency locks, notices and file-hash manifest.
   CI also builds the Docker image and probes both installed environments.
5. Verify fresh setup, an update over populated data, recovery, and the real broker
   journey before marking a release supported. Passing structural or controlled
   numerical checks alone does not establish that final acceptance.

## Current verification

Preview `0.1.0-preview.4` targets OpenAlgo 2.0.2.2, Python 3.12, VectorBT 0.28.5,
Optuna 5.0.0 and optional NautilusTrader 1.231.0. The final Windows research suite
passed **774 tests with 42 skips** and one existing Pydantic warning. Native
interface checks, Linux/Nautilus calculations and the Windows/WSL process boundary
have also been exercised. See [STATUS.md](STATUS.md) for the current evidence.

The preview.4 ZIP was installed into a fresh Windows directory and new locked
core environment. Setup/login, built assets, two-strategy real VectorBT and Optuna
calculations, exact selected replay, restart and exported-report preservation
passed, along with **40 artifact checks**. Graceful shutdown left no owned
processes, listening test ports or worker lease. This used an existing Windows
host and cached packages with controlled Historify candles, not a clean OS image
or a real broker account. Publication-only documentation edits followed this
artifact check; release checksums identify the final package.

An earlier fresh Linux installation passed with new locked core and Nautilus
environments, real HTTP-to-worker calculations and exact replay. Reinstalling the
same preview over populated data preserved the account, configuration and exact
exported reports. This proves an idempotent reinstall, not a cross-version schema
migration.

Real Fyers daily data use and missing-minute acquisition were checked separately
inside an existing OpenAlgo installation. The calculation snapshots matched its
Historify archive. This does not certify every broker or historical symbol.
Docker execution and broad cross-version upgrade coverage remain open. The
verified NSE calendar correction is the targeted migration described above.
