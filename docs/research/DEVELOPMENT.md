# Scanner Research development and operations

Scanner Research is a native OpenAlgo tool at `/scanner-research`, linked from
Tools. Account authentication and CSRF protect research APIs. Saved evidence
remains available without a current broker token; trading guards are unchanged.

## Calculation runtime

The `research` dependency extra installs tested VectorBT 0.28.5 and Optuna 5.0.0
without upgrading the core OpenAlgo data stack: `uv sync --frozen --extra research`.
Use the same environment for web and worker, and retain the extra when invoking
uv, for example `uv run --extra research python -m services.scanner_research_worker`.
The research distribution includes these packages; saved legacy reports remain
readable without them. The authenticated connector catalog uses package metadata and does not
import numerical engines into Flask/eventlet. Full imports and first-use Numba
compilation happen in the external worker with a live, fenced lease.

New runs use VectorBT and Optuna for optimization, without a legacy engine picker.
Incompatible requests are blocked without changing the user's strategy settings.
The exact supported scope, execution-policy differences, bounds and remaining
integration stages are documented in [CONNECTORS.md](CONNECTORS.md). Saved results
remain readable when packages are absent; recalculation requires the recorded
compatible versions.

## Isolated preview

Install dependencies with `uv sync --frozen --extra research`, then `npm ci` and `npm run build`
in `frontend`. From the checkout run `uv run --no-sync python tools/research_dev.py` and
open `http://127.0.0.1:5107/scanner-research`.

The loopback harness uses a disposable identity and owns one external research
worker, including graceful shutdown and process reaping. `--no-worker` allows
independent worker lifecycle tests. It never imports full application startup,
strategies, broker credentials or operational databases. It accepts explicitly
synthetic fixtures and reviewed public evidence. Do not deploy this harness or
use it for private data.

An immediate worker failure stops preview startup with an actionable error and
the worker-log path. After a forced host kill, wait for the 120-second stale
lease to expire before restarting; do not clear the lease manually. A forced
host kill cannot execute Python cleanup. Later worker failures remain visible
through the health endpoint and worker log; this harness is not a production
process supervisor.

Data lives under ignored `.agent-native/dev/research`. The preview prefers a
build copied to `.agent-native/dev/frontend`, allowing tracked distribution
files to retain their upstream state. Rebuild that preview after source edits.
An optional ignored `.agent-native/dev/settings.json` object may specify
`RESEARCH_PUBLIC_EVIDENCE_DIR`, pointing to the reviewed public bundle described
in [DATA.md](DATA.md). The bundle contains public archives, not credentials.
No configured bundle produces an actionable preparation error.

CSV uses Date and Symbol columns. The example CSV demonstrates the format;
scanner membership must come from the user's dated observations. A generated
membership sample cannot establish historical scanner performance.

## Native integration

### Build a reviewable native candidate

Run `uv run --no-sync python tools/research_release.py` from the feature checkout with Git,
Node and npm available. It stages allowed application source, installs the frozen
frontend lockfile with `npm ci`, builds the interface and creates a unique ignored
`.agent-native/release/<id>/openalgo-research-<version>.zip`. It leaves the checkout's
tracked `frontend/dist` unchanged. `manifest.json` (also `RELEASE_MANIFEST.json`
inside the archive) records the base commit, included uncommitted changes, every
source/asset SHA-256, dependency lock hashes and Node/npm versions. `SHA256SUMS`
identifies the archive. Build logs remain beside it for diagnosis.

The candidate includes native source, current assets and the OpenAlgo licence;
it excludes operational data, secrets, private workspace files, development
catalogues and raw price archives. It is a source/build distribution, not a
preconfigured running server or proof of a successful production deployment.
Extract into a new release directory and follow [DISTRIBUTION.md](DISTRIBUTION.md).
The supplied Docker launcher checks compatibility, initializes metadata and
supervises the app and worker together. Native broker prices use the installation's
Historify archive; the earlier public reference bundle is not required for this
native policy. Do not reuse the disposable preview authentication.

The ZIP has sorted entries, fixed timestamps and modes. Byte-for-byte rebuilds
also depend on the recorded toolchain; artifact hashes are the definitive identity.
The standard Dockerfile performs its own native build. Docker, HTTPS/proxy and
actual-host verification remain applicable even when the local candidate passes.

### Register and configure research

Research uses the installation's existing native `HISTORIFY_DATABASE_PATH`,
including the native `db/historify.duckdb` default. Web and worker must load the
same path and native account configuration. The legacy optional
`RESEARCH_HISTORIFY_DATABASE_PATH`, if set, must match it; it does not select a
second database. No research-specific broker credentials or Fyers selection are
required. The current native broker is used only to fill missing prices.

For development set `RESEARCH_REQUIRE_ISOLATED_ARCHIVE=true` and place the
configured Historify file inside the isolated research data directory. The
preview sets this guard automatically and continues to block broker access.
Development checks must never point at the user's live archive or credentials.
The validation reference in [DATA.md](DATA.md) applies to the earlier public-price
policy; it is not required for the native broker-price policy.

The full app registers the authenticated research blueprint. SQLAlchemy metadata
uses OpenAlgo's NullPool engine factory. Private `research_data` is the default,
overridable with `RESEARCH_DATA_DIR`; `research.db` and `artifacts/` belong together.
The registered `upgrade/migrate_scanner_research.py` migration supports `--status`
and idempotent fresh/populated initialization, adding missing metadata tables.

The production web process does not launch a worker itself. The distribution
supervisor starts one external worker with the same data directory. For manual
development use `uv run --no-sync python -m services.scanner_research_worker`.
`--once` handles one queued job; `--stop-file PATH` cooperatively exits when a
supervisor creates the named file. See [RUNTIME.md](RUNTIME.md) for the shared
launcher; this local build has not been deployed. Use a normal logging format
such as `%(levelname)s %(name)s %(message)s` for `LOG_FORMAT`.

### External worker supervision

Use one worker per research directory, with the web process and worker running
the same checkout and dependencies. A running process alone does not establish
readiness: the authenticated `/scanner-research/api/health` response must report
`worker_state: online`. `offline`, `stale` and `maintenance` are separate states.
An immediate import, directory or lease failure exits nonzero; inspect the worker
log. Never erase a lease to force a second worker to start.

For Linux, this concrete systemd configuration is an operator template. Replace
the account and absolute paths for the intended isolated installation; it has
not been installed or exercised under systemd in this Windows development run.
The environment file must supply the same `RESEARCH_DATA_DIR` as the web process.
The stop command waits for cooperative exit because systemd otherwise terminates
remaining processes when `ExecStop` returns. See the
[systemd service reference](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml).

```ini
# /etc/systemd/system/openalgo-research-worker.service
[Unit]
Description=OpenAlgo research calculation worker
After=network.target

[Service]
Type=exec
User=openalgo
WorkingDirectory=/opt/openalgo
EnvironmentFile=/etc/openalgo/research-worker.env
RuntimeDirectory=openalgo-research
ExecStartPre=/usr/bin/rm -f /run/openalgo-research/stop
ExecStart=/opt/openalgo/.venv/bin/python -m services.scanner_research_worker --stop-file /run/openalgo-research/stop
ExecStop=/bin/sh -c 'touch /run/openalgo-research/stop; while test -n "$MAINPID" && kill -0 "$MAINPID" 2>/dev/null; do sleep 1; done'
TimeoutStopSec=45
Restart=on-failure
RestartSec=130
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Example environment file (paths are placeholders; keep this file outside Git):

```dotenv
RESEARCH_DATA_DIR=/var/lib/openalgo-research
LOG_DIR=/var/log/openalgo-research
LOG_FORMAT="%(levelname)s %(name)s %(message)s"
```

The service account needs write access to those directories. Public evidence and
acquisition settings are optional and follow [DATA.md](DATA.md). Configure any
account database access through the installation's existing protected server
configuration; do not place broker tokens in command arguments. The 130-second
failure restart delay allows the 120-second lease to expire after a crash.
Graceful stops release it immediately. A timeout or host kill can leave interrupted
work needing an explicit Resume; queued work is picked up after restart.

On Windows the supported local launcher is `uv run python tools/research_dev.py`;
it owns and reaps its worker. To operate the external CLI independently, use a
dedicated PowerShell session in the checkout with the isolated environment set:

```powershell
$env:RESEARCH_DATA_DIR = 'C:\OpenAlgoResearch\data'
$env:LOG_DIR = 'C:\OpenAlgoResearch\log'
$env:LOG_FORMAT = '%(levelname)s %(name)s %(message)s'
# Remove only this stop marker, after confirming the prior worker has exited.
Remove-Item -LiteralPath 'C:\OpenAlgoResearch\worker.stop' -ErrorAction SilentlyContinue
& .\.venv\Scripts\python.exe -m services.scanner_research_worker --stop-file 'C:\OpenAlgoResearch\worker.stop'
```

Create that exact stop marker from a second session to request shutdown, then
wait for the worker process to exit and health to become offline before restarting:

```powershell
New-Item -ItemType File -Path 'C:\OpenAlgoResearch\worker.stop' -Force
```

If an operator wraps this command with Task Scheduler or a Windows service
manager, the wrapper must preserve the working directory/environment, allow only
one instance, wait for cooperative shutdown, and delay failure restart by at
least 130 seconds. These are supervisor requirements, not a claim of a tested or
installed Windows service. The CLI itself does not implement automatic restart.

### Locally verified integration boundary

`test/research/test_supervision.py` starts the real continuous worker CLI against
temporary storage, observes authenticated health become online, requests graceful
stop, checks lease release, restarts it, and verifies a job queued while offline
completes. It also checks an invalid storage path exits unsuccessfully without
publishing a healthy lease. Existing job tests cover one-shot subprocess work
and fenced stale-lease recovery. These tests use synthetic evidence, no broker
credentials, and start no system service.

`test/research/test_native_app_integration.py` imports actual `app.py` and executes
its application factory, research registration, React route handling, signed
sessions, global session-expiry guard and Flask-WTF CSRF. Unrelated blueprints,
database initializers, schedulers and outbound/background services are disabled
dependencies in that harness. It verifies research access with a valid account
without a broker session, rejection of expired account sessions, and CSRF
enforcement. This is a tested native application boundary; it does not establish
live broker connectivity or full hosted startup. The disposable preview above
uses its own Flask host and identity and is a separate UI fixture.

The release-preparation smoke separately ran real `app.py` with its full native
empty-installation startup under one Gunicorn/eventlet web worker on isolated
Linux loopback ports. Native admin setup/login, protected research, CSRF,
official-price preparation, external calculation, exact export, worker restart
and new-directory backup/restore passed without mocked modules. Every owned
process exited and its ports were clear. This adds real startup evidence to the
focused regression harness; it does not test an external HTTPS proxy, installed
supervisor or broker session. See [VERIFICATION.md](VERIFICATION.md).

Historify inspection requires explicit `HISTORIFY_DATABASE_PATH` and never
creates a missing archive. Bare candles cannot prove identity or adjustments.
The acquisition adapter and checked public snapshot workflow are documented in
[DATA.md](DATA.md), including the remaining authorized live-broker validation.

## Jobs, recovery and evidence

A serialized admission transaction permits four active/queued jobs. A client
request identity plus canonical payload hash makes lost-response retries reuse
one job; different inputs under the same token fail. Sources are immutable and
owner-scoped. Status polling returns metadata rather than decompressing prices.
Source menus and reloadable drafts use compact receipts with explicit detail
counts; per-bar lineage and complete gap lists remain in exact saved evidence.

One worker holds a fenced lease renewed during work and idle periods. After
120 seconds without a heartbeat, a replacement can mark old work interrupted;
queued jobs survive. Search/research checkpoints retain the exact input/config/
policy identity and completed work. An explicit Resume requeues the same job;
a mismatched checkpoint is rejected. Resuming and extending a completed search
are distinct actions. A follow-up must cite its completed parent and exact
previously evaluated indices; it cannot invent coverage of the search grid.

Cancellation is checked during bounded calculation progress and before
publication. Queued cancellation is immediate; completed evidence is immutable.
Navigation, reload and draft edits do not cancel server jobs. Process shutdown
interrupts work, releases its lease and reaps the child. A forced crash may
require the lease timeout before replacement.

Canonical JSON is SHA-256 addressed, gzip compressed and atomically published
through a synced temporary file. Legacy plain JSON remains readable. Result
bundles share their immutable input artifact; each search row stores summary
metrics rather than a duplicate price snapshot. Export resolves shared inputs,
retaining original CSV bytes/hash, normalized signals, exact prices/calendar,
provenance, configuration, execution policy, findings, curves and ledgers.
`X-Evidence-SHA256` hashes the expanded exported bytes; the stored artifact hash
is separately labelled. Reopening never refreshes prices or recalculates.

Inputs are bounded to 8 MiB, 25,000 signals and ten years. Snapshots cap at 3,000
sessions and two million bars. Grids cap at 50,000 settings; one search evaluates
at most 5,000, earlier-only searches at most 1,000 per fold and research at most
five folds. These are admission bounds, not full-capacity performance claims.
Artifacts cap at 128 MiB decoded / 32 MiB compressed. Admission checks the
configured 2,048 MiB default storage quota against every file in the research
directory, including acquisition receipts, isolated Historify and official
extensions. Bounded writes can exceed it before the next progress check;
metadata growth and filesystem overhead are not a strict filesystem reservation.
Exports are bounded in memory by saved artifact limits, not streamed.

All saved work is retained until an explicit operator decision. Orphan pruning
is a dry run by default and never expires referenced evidence. Consistent
backup, verified restore into an empty directory, maintenance fencing and safe
pruning are documented in [STORAGE.md](STORAGE.md).

## Research interpretation

Fixed setups, search, sensitivity and chronological research share one evaluator.
Search reports real evaluated counts, bounded nearby-setting comparisons,
recorded recommendations and alternatives. Quick/full sample evenly; Auto adds
nearby candidates after a broad pass; exhaustive covers the remaining grid.
Sensitivity holds the selected setup fixed. Later tests use either a fixed setup
or selection from earlier data only, with explicit gap and disjoint later folds.
Each fold starts with fresh capital; reports do not fabricate a combined equity
curve. Known overlap from previous backtests, search, sensitivity and research
is recorded regardless of CSV ordering or the user's unexplored checkbox.
This cannot prove that data was never seen outside this store.

See [EXECUTION.md](EXECUTION.md) for financial semantics and corrections.
[STATUS.md](STATUS.md) records measured verification and external limitations.

## Decisions and evidence use

Saved comparison members are the first decision entry point. Native Decisions
store Keep/Reject/Revisit choices with optional reasons and append-only revisions.
Exact source/configuration/period identifies the candidate; comparison and optional
later report/analysis pins identify each revision's supporting versions. A user
choice does not change the optimizer winner or launch a worker.

Later evidence admission verifies original lineage and retained reserved prices
and signals. Decision context is requested on demand; list/history use metadata.
Successful explicit report actions acknowledge Opened through a CSRF-protected
POST, separate from read-only GET and legacy unknown viewing. Archived choices
remain readable; report opening observations may still be recorded. Changing a
choice requires a new revision and stable retry token.

Four additive tables and retained artifact roots support populated backup and
old-store restore. The validator accepts native `validation` requests with the
same exact version/job linkage required for replay. Limits, tested boundaries and
the deferred canonical comparison-to-validation adapter are in
[the decision receipt](REPORT_EXPERIENCE_PLAN.md#20-saved-decisions-and-evidence-use--12-september-2026).
