# NautilusTrader portfolio connector

The optional v2 connector runs a real NautilusTrader `BacktestEngine` in a separate
calculation runtime. OpenAlgo continues to supply authentication, normalized
signals, native historical prices, saved jobs and the consumer interface. The
connector has no broker client, network download or live-order route.

The implemented interface is:

```python
evaluate(strategies, snapshot, capital, *, progress=None)
```

`validate_config` and `validate` are available without importing Nautilus. Requests
and results use the portfolio connector contract. The report contains combined
equity and cash, one trade ledger with strategy identity, per-strategy native PnL
attribution, and retained native orders, positions and fill events.

## Install the tested runtime

The tested runtime is **NautilusTrader 1.231.0, Python 3.12, Linux x86-64**,
including Ubuntu under WSL. It pins NumPy 2.4.4, pandas 2.3.3 and PyArrow 25.0.1 in
[`research/runtimes/nautilus/pyproject.toml`](../../research/runtimes/nautilus/pyproject.toml)
and its `uv.lock`. OpenAlgo's main environment retains PyArrow 23.0.1; Nautilus's
newer Arrow dependency is installed separately. VectorBT and Optuna continue to
run in the ordinary Windows or Linux core environment.

From the repository root on Linux, with Linux `uv` available:

```sh
uv run --no-project python tools/research_install_nautilus.py
```

For a Windows-hosted application, open its checkout under `/mnt/<drive>` in the
chosen WSL distribution and run the installer there:

```sh
uv run --no-project python tools/research_install_nautilus.py --wsl-distribution Ubuntu
```

Use the actual installed distribution name. This command uses Linux `uv` and
Python, not the Windows executables. It copies **only the two pinned dependency
manifests** into `~/.cache/openalgo-research/<checkout-hash>/nautilus`, installs the
frozen dependencies there, and probes the real native engine. Keeping the runtime
on Linux avoids the slow native imports observed from a virtual environment on
`/mnt/<drive>`. The Windows application and all account, strategy and price data
stay in place.

An explicit Linux location can be supplied with `--runtime-dir`, for example:

```sh
uv run --no-project python tools/research_install_nautilus.py \
  --wsl-distribution Ubuntu --runtime-dir ~/.cache/openalgo-research/nautilus-preview
```

After a successful probe the installer atomically writes
`research_data/nautilus-runtime.json` in the application checkout. The bridge reads
this configuration, or the path explicitly supplied through
`RESEARCH_NAUTILUS_CONFIG`. A failed install or startup check preserves the previous
configuration. Refresh the research screen, open **More settings → Backtest
engine**, and select NautilusTrader. Unsupported slippage or trailing rules must
be adjusted explicitly; selecting another engine does not silently change them.

A native Linux installation defaults to `research/runtimes/nautilus/.venv`.
The Dockerfile includes that locked environment in the Bookworm image, although
this preview's Docker build and fresh-container journey remain unverified locally.
No separate Nautilus web application or broker login is required.

The connector does not use direct Windows native execution: the tested Windows
1.231.0 wheel hung while importing its account-event extension. The Windows-to-WSL
bridge is verified. This describes this connector's supported route, not every
possible upstream Windows installation.

## Dependency updates

Keep the tested side-runtime manifests and lock together. Re-run the installer
when a distribution changes them; do not upgrade Nautilus or Arrow in the core
OpenAlgo environment. For a reversible runtime upgrade, keep the old runtime and
install the new release into a different `--runtime-dir`, retaining the previous
configuration alongside the application backup. A changed engine version needs
adapter, numerical, bridge and saved-evidence regression checks before release.
The calculation child checks Python and all four pinned package versions before
running the engine. Existing reports retain their original policy and version.

Primary references: [installation](https://nautilustrader.io/docs/latest/getting_started/installation/),
[1.231.0 package metadata](https://pypi.org/project/nautilus-trader/1.231.0/), and
[upstream release notes](https://github.com/nautechsystems/nautilus_trader/releases).

## Supported strategy and data scope

- Multiple long cash-equity scanner strategies share **one INR cash account**.
- Each strategy has its own native Strategy ID; each scanner signal has its own
  native Position ID. Repeated symbol/date/source-row combinations remain distinct
  across strategies and within a strategy.
- Daily and one-minute snapshots are supported. Timestamped observations enter
  strictly later; date-only observations enter next session. Minute holding
  limits, entry/exit clocks, intraday session close and multiday session holding
  reuse the common causal scheduling rules.
- Strategy order followed by CSV order determines competing opening entries.
  Allocation is a maximum deployed share of opening account equity; per-signal
  size is a share of that allocation. These are not separate cash accounts.
- Fixed stops, targets, strict whole-share sizing and independent per-strategy
  fees are supported. **Trailing stops and configured nonzero slippage are
  explicitly rejected by this first adapter.** There is no silent engine fallback.
- Each symbol that can enter needs supplied instrument metadata in
  `snapshot.instruments`: `currency: "INR"`, `lot_size: 1`, `tick_size`, and
  `price_precision`; ISIN is optional. The adapter never guesses tick size or
  rounds a broker candle to fit absent metadata.

## Supplied instrument eligibility

Adapter `nautilus-portfolio-adapter-v2` records eligibility policy
`nautilus-supplied-grid-windows-v1`. Before any optimizer trial, the workflow
freezes each signal's maximum possible holding window and checks its raw OHLC
against supplied instrument tick size and precision. It keeps a stable eligible
cohort across every candidate. Missing prices, an unavailable master entry or
incompatible window prices retain explicit signal exclusions and their original
source rows. Entirely unusable input fails clearly.

The metadata comes from OpenAlgo's current NSE master contract. That is a supplied
execution specification, **not proof of the historical tick grid**. The workflow
does not infer old specifications, round historical broker candles or replace
prices to obtain more eligible trades. Incompatible candles outside all potentially
active windows do not invalidate unrelated strategies or signals. The recorded
scope includes the full optimizer holding range, not just sampled trial values.

Original prices and saved v1 reports remain unchanged. A v1 report retains its v1
adapter identity; numerical replay requires the recorded compatible adapter.
Incomplete snapshot tails retain open/pending positions; no future candle or
closing trade is invented.

The full-input v2 eligibility check records 298 eligible signals and 63 exclusions:
57 supplied-grid mismatches, one unavailable master entry and five price gaps.
The complete native calculation on that cohort passed through ordinary native
price preparation, the Windows worker and WSL bridge: 194 closed, three pending
and 101 cash-skipped signals. All 1,995 frozen daily OHLC rows matched Historify,
with original source and evidence unchanged. [STATUS.md](STATUS.md) records the
local acceptance boundary; this does not certify every broker or historical grid.

## Execution policy: `nautilus-ohlc-low-first-v1`

Native bar callbacks arrive after the native bar's execution sweep, so submitting
from `on_bar` and asking to use that same bar's open would be incorrect. Instead,
the adapter feeds explicitly **modelled OHLC-derived zero-spread quote events**
and separate control events to the native engine. Each bar follows
`Open → Low → High → Close`; control events ensure every symbol's opening mark is
available before strategy entries. Native standing protection can fill at the
opening update. Opening time exits then settle, followed by all opening entries.
Within-bar or closing proceeds cannot fund earlier entries.

These events are a deterministic OHLC execution model, **not observed broker
ticks or reconstructed market depth**. Nanosecond offsets establish model order
and are not real crossing times. Quotes have a bounded, large model liquidity;
this adapter does not make a capacity claim. Daily versus minute runs can differ
because the available chronology differs.

Nautilus owns native market/stop/limit matching, commissions, cash and positions.
The connector submits native protective orders and cancels the sibling when one
fills. Stops round downward and targets upward to supplied tick increments.
Native commissions round to INR precision. Native results intentionally need
not equal VectorBT's fill policy: in a tested entry bar with open 100, stop 95 and
low 90, the native stop market order fills at the modelled low quote of 90;
VectorBT's conservative threshold policy fills at 95. The distinct engine and
policy remain attached to each saved result.

Per-strategy PnL comes from native position realized and marked unrealized PnL.
Every bar is checked against native account cash plus native marked position
notionals. Contributions are percentage points of initial total portfolio
capital. Per-strategy equity is attribution against initial allocation, not an
independent simulation or a reserved balance. Native IDs/events are retained in
the exact saved report; a numerical rerun need not recreate randomly generated
event UUIDs.

## Verification and resource bounds

The v2 native suite passes **29 Linux checks** using real Nautilus calculations,
including shared cash, repeated positions, fees, timing, protection, frozen grid
eligibility and repeated engine disposal. **Ten metadata checks** exercise native
master lookup, unavailable specifications and stable maximum-window exclusions.
These calculations are not replaced by mocked account results. The earlier
combined native/runtime proof passed 59 Linux checks; these are separate,
overlapping verification runs, not additive totals.

The runtime suite passes **29 Windows checks**, with eight Linux-only cases
skipped. Its opt-in real WSL case cancels a noisy numerical child that ignores
SIGTERM even after its process record is corrupted. The supervisor kills and
reaps that owned child. Repeated real core-to-WSL calculations preserve the
hand-checked shared-account result of 1,000 starting cash to 1,075 final equity;
five consecutive runs kept the same Windows process-handle count and left no
temporary job directories. These are controlled calculation fixtures, not a
broker-download acceptance test.

Linux developer verification, from the repository root:

```sh
uv run --no-project python tools/research_install_nautilus.py
uv sync --project research/runtimes/nautilus --python 3.12 --frozen --no-dev --group test
PYTHONPATH=. research/runtimes/nautilus/.venv/bin/python -m pytest \
  --confcutdir=test/research test/research/test_nautilus_portfolio.py \
  test/research/test_nautilus_runtime.py -q
```

When an explicit or WSL cache directory is used, substitute that project path for
`research/runtimes/nautilus` in the test-environment commands. On Windows, the
configured bridge cancellation test is opt-in; it uses only its own synthetic
child and temporary directory:

```powershell
$env:RESEARCH_NAUTILUS_INTEGRATION = '1'
uv run --no-sync pytest test/research/test_nautilus_runtime.py -q
```

CI installs the frozen side runtime on Linux, runs the native and lifecycle suites,
and invokes the real bridge from OpenAlgo's core environment. Docker CI builds the
image and probes the native engine there; this is a release check to run, not a
claim of a completed local Docker verification.

Admission is bounded to 2,500 signal lots, 100,000 bar/lot cells and 200,000 native
market/control events. All event lists, strategy state and retained reports are
evaluation-local. The `BacktestEngine` is disposed in `finally` on success, errors
and cancellation. It starts no broker client or live-order route.

The calculation bridge adds a separate Linux supervisor so a blocked native call
cannot prevent cancellation handling. It publishes process identity and progress
atomically, checks launch identity before fallback signals, and escalates from
cooperative cancellation to terminating and killing its owned numerical child.
Every directly owned process is waited for. Temporary job directories are removed
on success, error and cancellation.

Input and output JSON are bounded to 128 MiB during writing and reading; progress
and process metadata have smaller bounds. Errors are truncated to 2,000 characters,
child console streams go to the null device, and calculations have a 30-minute
limit. The installer retains a bounded status receipt, enforces an exclusive
per-runtime lock, limits dependency installation to 15 minutes and its native
startup probe to 60 seconds. Timeout cleanup terminates and reaps the owned process
group. WSL clears the Linux environment before Python starts; calculation and
installation children do not inherit application credentials.
