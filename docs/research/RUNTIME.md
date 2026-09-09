# Running the research distribution

OpenAlgo Research runs inside the normal application. The web process provides
login and the **Tools → Backtest & Optimize** screen; an external worker owns price
preparation and calculations. Use one research worker per research directory.
VectorBT and Optuna use the core Python environment. Nautilus calculations use a
separate locked Linux environment through the same worker and consumer interface.

## Native Windows or Linux core

Download the [versioned preview ZIP](https://github.com/mamamiya7/openalgo-research/releases/tag/research-v0.1.0-preview.4)
and extract it. Use Python 3.12 and `uv` from that installation directory. A release ZIP already
contains the built interface; Node/npm is needed only when building from a source
checkout. On a fresh Windows installation, open PowerShell in the extracted
directory and create the configuration once:

```powershell
Copy-Item .sample.env .env
```

Use this copy command only when `.env` does not already exist. Keep an existing
installation's configuration during updates. Configure its broker connection and
local address in `.env` using the normal OpenAlgo setup. First startup replaces
the sample security-key placeholders with unique installation keys; preserve the
resulting `.env` afterward. Account creation happens in the setup screen.

Install and check the tested core dependencies:

```sh
uv sync --frozen --extra research
uv run --no-sync python tools/research_check.py
uv run --no-sync python upgrade/migrate_scanner_research.py
```

Start the app in an interactive terminal using its normal native command:

```sh
uv run --no-sync python app.py
```

Wait for the app's **Ready** message, then start the research worker from a second
terminal opened in the same installation directory, or its existing process
manager. Both processes must use the same environment and data paths:

```sh
uv run --no-sync python -m services.scanner_research_worker
```

The interactive app starts its WebSocket proxy; do not start another proxy for
this launch method. It does not start the research worker. Open the usual
application address, normally `http://localhost:5000`, complete the one-time
account setup, and select **Tools → Backtest & Optimize**.

Keep both terminals open while using research. Stop the research worker with
**Ctrl+C** first, allowing its current work to checkpoint, then stop the app with
**Ctrl+C**. Restart with the same two commands. Review completed runs or resume an
interrupted run after signing in. Closing a terminal forcibly is not a graceful
worker shutdown.

The authenticated `/scanner-research/api/health` endpoint must report
`worker_state: online` before submitting a calculation. If a worker stopped
unexpectedly, let its lease expire before restarting it; do not erase the lease
to force a second worker. Saved runs have an explicit resume action.

Native Windows operation supports VectorBT and Optuna. To enable Nautilus, install
its Linux side runtime through WSL as described in [NAUTILUS.md](NAUTILUS.md).
Only numerical requests cross that boundary: account credentials and broker
clients stay in the ordinary OpenAlgo worker. The POSIX production supervisor
below is not the native Windows launcher. The interactive `app.py` command uses
Werkzeug; it refuses a detached non-interactive launch. Use the managed Linux
service below for background operation on Linux.

The preview.4 ZIP has been exercised from a fresh extracted Windows directory
and new locked Python environment on an existing Windows host: setup, account
login, two-strategy VectorBT, real Optuna trials, exact selected replay, restart
and export preservation. All 40 artifact checks passed. Native Ctrl+C shut down
the app and worker successfully, with no owned processes, listening test ports
or worker lease remaining. This used controlled Historify candles and package
caches, not a clean operating-system image or a broker login. Publication-only
documentation edits followed that artifact check.

## Managed Linux source installation

The supplied Linux supervisor uses Gunicorn and Eventlet. These production
dependencies are installed by the Dockerfile and are separate from the frozen core
research extra. The fresh source check used **Gunicorn 25.3.0 and Eventlet 0.41.2**.
For a managed source installation, install these after the core dependency sync:

```sh
uv pip install --python .venv/bin/python "gunicorn==25.3.0" "eventlet==0.41.2"
uv run --no-sync python tools/research_runtime.py --port 5000
```

Use the installation's web port in `--port`. This supervisor starts the web app,
WebSocket proxy and research worker together; use it as the owner of those three
processes. It binds the production web server to all interfaces on that port.
The interactive native command instead honors `FLASK_HOST_IP`. Repeat the
production-dependency installation after a core `uv sync`, which removes packages
outside the core lock.

## Docker service

The supplied Docker configuration uses Python 3.12 on Debian Bookworm, installs
the frozen core research extra, and includes a separate frozen Nautilus environment
at `research/runtimes/nautilus/.venv`. Startup checks core compatibility before
migrations. Its supervisor owns the normal single-worker eventlet web server,
WebSocket proxy and bounded research worker.

```sh
docker compose up --build -d
```

If any supervised service exits, the supervisor stops the others and Compose can
restart the complete service. It does not silently restart individual jobs. The
image build and startup path are implemented and checked by the compatibility
workflow; a local Docker build and fresh-container journey remain unverified for
this preview.

The `openalgo_db` volume holds OpenAlgo's operational databases and default
Historify archive. `openalgo_research` holds research metadata, inputs, snapshots,
checkpoints and reports. Strategies, keys, logs and temporary runtime state have
their own volumes. The app and worker share `RESEARCH_DATA_DIR`; custom native
Historify paths must also remain identical in both processes. The launcher does
not create a second broker configuration or authoritative candle database.

```sh
docker compose stop
```

Shutdown asks the worker to save progress, allows graceful web shutdown and then
terminates remaining owned processes. The image's `tini` reaps orphan descendants;
Compose allows 60 seconds. Interrupted saved runs retain their explicit resume
behavior. A forced machine shutdown may require the 120-second worker lease to
expire before recovery.

## Nautilus process boundary

A calculation exchanges bounded JSON files with a short-lived Linux supervisor.
That supervisor starts and reaps the numerical child, including a native call that
ignores cooperative cancellation. The bridge checks progress, cancellation,
timeout, output structure and engine version. Launch identities prevent fallback
cleanup from signalling unrelated processes. Input and output files are published
atomically, limited to 128 MiB and removed with their private job directory.
Child console output is discarded; actionable errors are returned as bounded JSON.
The calculation limit is 30 minutes.

On WSL the bridge clears the Linux environment before starting Python. The child
receives no broker tokens or application database configuration, and does not
import the web app. The installer likewise retains only operating-system paths,
uses a per-runtime installation lock and publishes configuration after the native
startup probe succeeds. Details and tested limits are in [NAUTILUS.md](NAUTILUS.md).

## Preserve data during updates

Stop the application and worker before replacing code. Back up `.env`, account and
Historify data, strategies, the complete research directory and any side-runtime
configuration. Preserve the same data paths and Compose project/volume names.
Do not use `docker compose down --volumes` during an update: it deletes saved data.

Install a tested release and its frozen locks. Keep a restorable source/runtime
and data backup before a migration; existing reports retain their recorded engine,
policy, prices and settings. A failed new broker download does not invalidate or
rewrite older saved results. Distribution and release checks are documented in
[DISTRIBUTION.md](DISTRIBUTION.md).

For this release's NSE calendar correction, run the targeted migration after
replacing source and before restarting:

```sh
uv run --no-sync python upgrade/migrate_research_nse_calendar.py --status
uv run --no-sync python upgrade/migrate_research_nse_calendar.py
```

It preserves custom entries and saved evidence. The update sequence omits the
older wholesale calendar reset. A fresh installation initializes the reviewed
calendar at native startup.
