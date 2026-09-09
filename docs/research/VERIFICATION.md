# OpenAlgo Research verification

Current checkpoint: 9 September 2026, `0.1.0-preview.4`. **Local acceptance is
complete for the declared native joint-portfolio scope.** Docker, cross-version
migrations beyond the targeted calendar update, hosted operation and universal
broker coverage remain unverified.
This checkpoint supports the [public preview source and release](https://github.com/mamamiya7/openalgo-research/releases).
The entries below retain earlier local checkpoints; they are not additive test counts.

## Final preview.4 checks

The full research suite completed with **774 passed, 42 skipped and one existing
Pydantic warning**. The exact preview.4 archive was then installed in a fresh
Windows directory and locked environment: setup, login, the built interface,
VectorBT calculation, Optuna TPE optimization, selected-trial replay, restart,
saved exports and process shutdown passed. Its 40 targeted archive tests passed.
This was a new directory on an existing Windows host with cached packages,
not a clean operating-system image. Test candles were controlled fixtures;
real Fyers daily and minute checks are recorded separately below.

Publication preparation changes documentation, ignore rules and CI triggers.
Application and engine sources remain the verified preview.4 revision. Packaging
and compatibility checks passed again (**35 passed, one symlink test skipped**).
The public ZIP is rebuilt from the tagged source and has its own manifest and
checksum; it is not identified by the earlier local candidate's checksum.

## Native Fyers no-data correction

Profiling the full saved portfolio separated calculation from acquisition:
VectorBT reproduced the exact result in approximately 6.5 seconds including cold
imports. The fresh run also queried missing windows even though it added no new
candles. Its nine recorded requests included four empty responses and five
unavailable master symbols. Zero new candles does not mean zero broker requests.

One affected window was checked directly through the installed Fyers adapter.
Its response was exactly `s=no_data`, `code=200`, `candles=[]`, with an empty
message. The ordinary adapter had been retrying this successful empty response
three times per window. It now advances without retries for this exact shape,
while genuine errors and malformed responses fail after bounded retries. A failed
later chunk no longer publishes the earlier chunks as a complete success. The
ordinary native service remains the data path; no second downloader was added.

The final Fyers, native history, price acquisition, service and compatibility
targets passed **105 checks**, including 16 new ordinary-adapter regressions.
This is a targeted run after the earlier 758-pass full-suite checkpoint. The
change adds no client, thread, cache or retained resource; existing pooled
transport remains in use and failed in-memory chunks are discarded on return.

## Preview.3 calendar, data and Windows update

The 9 September update uses reviewed NSE annual holidays and special-session
circulars to validate the required native calendar window. Fresh startup repairs
recognized shipped errors; a targeted upgrade does the same on a populated
installation. It does not reset administrator changes or other exchanges. The
old destructive all-exchange reset is no longer an automatic migration.
Unsupported years and unconfirmed required special-session hours fail before
price acquisition. Completed old evidence remains available for exact replay.

On the existing authorized installation, the minute test began with zero rows
for the required window. Fyers returned the historical response, six required
one-minute candles were persisted in the native Historify database, and VectorBT
completed. All six frozen OHLC candles matched Historify exactly. Replay matched
the summary, ledger, curve and contributions; it retained the saved receipt and
made no new acquisition. A receipt inherited by a replay is not a fresh download.

The full 361-signal daily run completed using 1,995 matching Historify candles and
zero newly downloaded candles. Its summary, ledger and strategy results matched
the previous run. The updated calendar removes unnecessary pre-signal padding:
160 curve points instead of 167. The old completed run replay retained its exact
167-point curve. Original CSV hashes, earlier artifacts, account configuration
and the installation's custom market-status component were unchanged.

The full Windows research checkpoint passed **747 tests, 42 skips**. The new
calendar, distribution and cooperative worker-signal targets then passed **37
checks**. The latter overlaps the former; it is not an additional total. CSV-help
UI verification passed **29 PortfolioJourney tests**, TypeScript, scoped Biome,
a production build and desktop/mobile keyboard/download checks. Example CSVs
passed the actual parser's daily/minute selection.

Final review reproduced a shortened special session that could not satisfy a
chosen entry clock. Calendar construction now waits for each strategy's actual
entry and deadline across the full optimizer range, instead of stopping after a
fixed session count. Twenty calendar checks and 52 surrounding checks passed;
the bounded price plan equals the full-calendar plan for skipped special-session
entries, multiday/minute deadlines and mixed portfolios. The final full Windows
research suite then passed **758 tests, 42 skips**, including the new signal tests.
The completion closure retains bounded signal/config/entry metadata only for its
single construction call and owns no connection or long-lived registry.

A preview.2 source ZIP was extracted into a new Windows directory with a new
frozen environment, empty databases and private ports. Setup generated the
installation keys; disposable-account login, a two-strategy VectorBT job, three
real Optuna TPE proposals, selected replay and restart passed using controlled
Historify candles. No owner database or broker credentials were copied. This
proves a fresh directory/environment on an existing Windows host, not a clean OS
image or new real broker acquisition in that environment.

Native terminal Ctrl+C exposed an abrupt worker interruption. The corrected
worker registers cooperative SIGINT/SIGTERM handlers, restores prior handlers
and closes its store even on startup failure. A five-of-six-trial interruption
resumed to the same result as uninterrupted execution. Windows signal/runtime
targets passed **eight checks with ten POSIX skips**; Linux passed **17 with one
Windows skip**. Native app and worker exited zero; eight owned processes were
reaped, all three private service ports closed and the worker lease cleared.

The targeted calendar update applied seven recognized changes on the populated
Windows fixture, then zero changes on repetition. Existing exports remained
byte-identical. A new calculation had the same summary, ledger and strategy
metrics; shared-date equity values matched while the curve shortened from 176
points to its three required-period points. This is an intentional planning
window change, not byte equality between new and old calculations.

Scoped resource review: the calendar helper uses the existing SQLAlchemy scoped
session, rolls back on failure and removes the session in a finally block. The
standalone migration disposes its engine. Repeated native snapshot tests balance
all connection checkouts/checkins. Calendar state is limited to the reviewed
two-year range and each call's bounded calculation window; no long-lived cache,
background thread or connection pool was added. The signal change owns only
local stop state and saved handlers; real shutdown checks cover cleanup.

## Current application acceptance

| Journey | Verified result |
| --- | --- |
| Full VectorBT fixed run | 361 original signals accounted for: 356 eligible and five explicit price exclusions. |
| Full Optuna workflow | 25 TPE proposals and an independent later-period check completed. |
| Exact selected replay | Summary, ledger, equity curve and strategy contributions matched the saved selection. |
| Representative intraday | All 61 required minute OHLC candles matched native Historify. |
| Full Nautilus v2 | Normal native price preparation → Windows worker → WSL calculation completed for 298 eligible signals; 63 exclusions retained. |
| Nautilus outcome accounting | 194 closed, three pending and 101 cash-skipped signals; 57 supplied-grid, one missing-master and five price-coverage exclusions. |
| Nautilus price evidence | All 1,995 frozen daily OHLC rows exactly matched Historify; original CSV/evidence hashes remained unchanged. |
| Consumer interface | Existing-account screen, saved signals, settings, progress, explicit exclusions, results and selected replay exercised. |

The minute calculation path changed from approximately 301 seconds to 6.4 seconds
in one local comparison, with identical financial output. This is an observed
before/after result, not a throughput or capacity guarantee. Private account
performance, source files, credentials and machine paths are not published here.

Nautilus uses `nautilus-portfolio-adapter-v2` and eligibility policy
`nautilus-supplied-grid-windows-v1`. Raw OHLC is checked against supplied metadata
over the full allowed holding window before trials. It is not rounded to force
compatibility. The current master is a supplied specification, not proof of the
historical tick grid. Earlier v1 reports retain their original identity and data.

## Automated checks

| Checkpoint | Result and scope |
| --- | --- |
| Full Windows backend | 742 passed, 38 platform skips. |
| Full Linux backend | 774 passed, 18 skips and one extracted-fixture inventory failure. |
| Linux fixture correction | 20 targeted checks passed after adding the required Git metadata only to the extracted test fixture; no production change. |
| Final Windows changed targets | 38 passed. |
| Focused frontend | 100 passed; final PortfolioJourney rerun 27 passed, with TypeScript validation. |
| Nautilus v2 native engine | 29 real Linux cases passed; accounting and fills are not mocked. |
| Native instrument metadata | Ten cases passed, including unavailable contracts and maximum-window eligibility. |
| Process/runtime boundary | 29 Windows checks passed, including a real WSL cancellation case; the earlier combined Linux engine/lifecycle proof passed 59. |

These are separate, sometimes overlapping runs and are not added together. The
Linux full suite was not represented as rerun in full after the fixture fix.
Coverage includes shared cash, repeated signal lots, native attribution, timing,
Optuna recovery, fixed cohorts, later-period isolation, exact exports, auth/scopes,
native daily/minute preparation and resource cleanup. Controlled broker responses
exercise additional adapters without claiming live verification of every account.

## Fresh installation and populated reinstall

The release inventory and built assets were staged into a new Linux source tree
with a new core environment, empty databases and independent loopback ports.
Frozen core installation, structural compatibility checks, schema initialization
and the locked Nautilus side installer passed. The managed service used Gunicorn
25.3.0 and Eventlet 0.41.2; its private test runner bound the web server to loopback.

The real setup endpoint and built screen loaded. A disposable account completed
setup/login. HTTP submission queued two-strategy VectorBT and real Optuna jobs
against deliberately controlled native Historify candles. Selected replay matched
exactly. The fresh core-to-Nautilus boundary also calculated successfully without
changing the core Arrow version.

After clean shutdown, the same preview source and locked dependencies were
reinstalled over populated isolated data. The account, configuration and exact
exports were preserved. Each shutdown reaped all four owned descendants; no
service ports or owned processes remained. This is an idempotent same-version
reinstall, not proof of a cross-version data migration. No real account or broker
credentials were copied into this test.

The ordinary interactive `app.py` command is distinct from managed Linux service
startup: detached Werkzeug refuses to run. [RUNTIME.md](RUNTIME.md) documents the
verified supervisor prerequisites. The Dockerfile includes a separate locked
Nautilus environment on Bookworm, but Docker was unavailable locally and no image
build/start success is claimed.

## Evidence retention and resource review

Original source and saved-result identities are checked before and after native
runs. Input, snapshot, trial and report artifacts remain immutable. Workflows use
existing account-scoped metadata, Historify and bounded external workers. JSON is
atomic and size-limited; process identities constrain fallback signals; cancellation
reaps a deliberately hung native child even with damaged process metadata. Repeated
real bridge runs retain neither temporary job directories nor growing handle counts.

Machine-specific receipts, logs, raw inputs and source hashes stay in ignored
`.agent-native/` storage. [DISTRIBUTION.md](DISTRIBUTION.md) records the deliverable;
[CONNECTORS.md](CONNECTORS.md) and [NAUTILUS.md](NAUTILUS.md) define supported policies.

## Historical scanner and first-connector checkpoints

The sections below retain earlier verification history. Their then-pending items
and narrower engine scope are historical unless repeated in the current boundary
above. They do not replace the current joint-portfolio acceptance record.

Verified 2026-09-06 after local audit corrections A1–A11. Automated checks use
isolated research stores; the separately authorized main-installation workflow
is recorded below. Original pre-audit receipts and completed reports remain intact.

## Audit acceptance evidence

| Audit | Correction and verification |
| --- | --- |
| A1 | Persistent identity quarantine, explicit dated mappings and boundary gaps. `test_data_audit.py` covers A→B→B, recovery, reviewed transitions, cutoff replay and held-position boundaries. |
| A2 | Eight prior scanner sessions for causal later-window triggers. `test_audit_math.py` compares full-history eligibility across strategies, warmup patterns, gaps/folds and future mutations without requiring warmup OHLC. |
| A3 | Cumulative distinct rows, incumbent, neighborhoods and pass counts; exact parent reports reused. Math tests cover better/worse/tied generations and resume; `test_continuation.py` verifies worker integration and unchanged parent exports. |
| A4 | Canonical categorical strategies; numeric neighbors share strategy and trailing state. Permutations preserve neighborhoods and adaptive selection; mode-only grids cannot imply numeric robustness. |
| A5 | Linked new attempts versus idempotent submission and same-job Resume. Attempt, pipeline and draft/page tests cover terminal recovery, owner scope, strict tokens, completed reuse and denied storage writes/reads. |
| A6 | Raw native Fyers outcomes and duplicates survive the adapter/history boundary. `test_native_history_evidence.py` covers expiry, exhausted retries, empty/partial data, conflicts and cancellation; pipeline tests check independent heartbeat during blocking I/O. |
| A7 | CSV → queued acquisition → expiry/missing-window recovery → checked source → run/export. Pipeline tests invoke real native adapter code with fake network responses. Actual official-extension worker tests use generated later-date archives; data tests reject conflicts and retain old snapshots. |
| A8 | Explicit trailing enabled/value choices, including enabled zero; parameterized math tests cover omitted/explicit axes, include-off choices and replay identity. |
| A9 | Dedicated complete preflight. Component/page tests and exact-width browser checks show all earlier/gap/later assumptions, sensitivity and workload before submission. |
| A10 | Decision/comparison-first reports, human labels and financial units, progressive ledgers and searchable/paginated saved work. Tests and browser checks cover alternatives, exact transfer, filtering and oldest retained export beyond 100 records. |
| A11 | Distinct worker states, joined acquisition heartbeat, real CLI lifecycle and actual app factory/guards/CSRF harness. Supervision, native integration, health and pipeline tests cover recovery and account/broker separation. |

Windows Python 3.12.10: **203 research tests passed in 38.67 seconds**, with the
reviewed public bundle configured. Linux/WSL Python 3.12.14: **208 passed in
57.39 seconds**, including the same suite and five SQLite/eventlet contention
cases; no optional bundle test was skipped. Frontend: **39 tests passed** across
page, draft, preflight, layout and authentication suites. Targeted Ruff/Biome,
TypeScript, Vite production build and whitespace checks passed. Four existing
Fyers `B904` findings outside the changed history code remain; whole-file lint
is not represented as newly clean.

The integration harness imports actual `app.py` and executes its factory,
React/research registration, signed account sessions, global expiry and CSRF.
Unrelated blueprints, database startup, strategy/subscriber threads and live
services are disabled before import. This verifies the native security boundary,
not actual broker login or full trading startup. Worker CLI tests start, observe
health, gracefully stop, restart queued work and verify nonzero startup failure
on invalid storage. No system service was installed.

## Intentional numerical and data differences

Current versions are `scanner-minute-causal-v1` for minute snapshots, `scanner-eod-next-open-marked-v3` for retained daily snapshots,
`scanner-experiments-v2` and `nse-public-evidence-v2`. Older completed results and
exports remain unchanged; incompatible queued/checkpoint work is rejected
explicitly. Later eligibility may differ because causal warmup was corrected.
Search conclusions can differ because all tested parent evidence now survives
follow-ups and categorical ordering no longer changes robustness.

Rebuilding the same generated 18,310-membership scale input with the corrected
importer retained **54,367 bars**, versus 54,691 before. The 324 additional
quarantined records belong to unexplained changed-identity segments: ANGELONE
128, BECTORFOOD 180, GROWWSLVR 1, IVZINGOLD 1, SILVERADD 5 and TDPOWERSYS 9.
Repeated observations do not verify those segments. This is intentional, not
a numerical tolerance change; old immutable inputs remain available.

The importer's seven-session prefix is a separate, explicit pre-CSV zero-count
assumption. An eighth zero session changes no earliest trigger: 1,536 read-only
comparisons across eight strategies produced identical complete ledgers and
summaries for seven versus eight zero-prefix sessions. Later folds need eight
sessions because their prior counts are known, rather than assumed zero.
Unknown historical scanner membership remains a source limitation.

## Representative external-worker run

`tools/benchmark_scanner_research.py` starts an isolated loopback Flask blueprint
host and real external worker, submits through HTTP, requests graceful shutdown
after a durable checkpoint, resumes that job, verifies rows and export, and
stops/reaps its processes. Source hashes before/after the run match.

Windows 11, Python 3.12.10, 16 physical / 24 logical cores, approximately 16 GiB
RAM. The final run shared the machine with other local checks.

| Measurement | Observed |
| --- | --- |
| Generated signals / symbols | 18,310 / 298 |
| Checked official bars / sessions | 54,367 / 193 |
| Evaluated configurations | 3,000 distinct; zero remaining |
| Grid | 20 targets × 10 stops × 15 holds; Bypass, trailing off |
| Checkpoint retained after restart | 30 exact result rows |
| Worker journey including interruption/resume | 541.31 seconds |
| Full submission/work/export journey | 548.84 seconds |
| HTTP submission | 0.278 seconds |
| Health p95 / maximum (934 samples) | 53.81 / 88.04 ms |
| Jobs metadata p95 / maximum (934 samples) | 58.42 / 112.13 ms |
| HTTP failures | 0 |
| Worker peak working set | 296.96 MiB |
| HTTP peak including expanded export | 485.87 MiB |
| Research storage / artifact files | 106.28 MiB / 602 |
| Result compressed / decoded | 4.48 / 52.49 MiB |
| Expanded export | 69.82 MiB; SHA-256 verified |

The exact export SHA-256 is retained in the private verification receipt.
Backup retained three referenced artifacts plus SQLite, **9,021,327 bytes**.
Restore into a new directory verified file/canonical hashes and all metadata
references. Unreferenced old checkpoints remain in the original store.

An earlier corrected run took 355.17 seconds; the pre-audit run took 175.86
seconds with older policy/data. These are observed local timings, not comparable
isolated trials or throughput guarantees. Other trigger/trailing grids have
correctness coverage but are not covered by this timing. Memberships were
generated for load testing, not recovered scanner selections; coverage warnings
remain visible. The benchmark uses disposable authentication and threaded
Flask, not full OpenAlgo/eventlet startup. Peak working set excludes filesystem
cache. JSON export is bounded in memory, explaining its larger HTTP peak.

## Browser and resource verification

Browser journeys exercised public preparation, fixed backtest, search, exact
alternative transfer, disjoint later windows, sensitivity, earlier-only
selection, saved reopening and export. The current-policy two-fold later run
completed 58 evaluations. Comparisons precede long ledgers. A named,
keyboard-focusable ledger region scrolls horizontally with arrow keys.

Exact **320 and 390 CSS-pixel** documents were checked in a same-origin iframe
whose explicit width was measured inside the application document. Desktop zoom
otherwise changes requested outer viewport dimensions. At 320, document
client/scroll widths were both 306 after the scrollbar; at 390 both were 377.
There was no page horizontal overflow; wide ledgers scroll in their own regions.
Material preflight fields and actions remained visible. Native light/dark
appearance and keyboard theme activation were checked without changing trading
mode. This is scoped visual/keyboard QA, not accessibility certification.

The isolated preview catalogue was supplemented with 102 explicitly labelled
generated cancelled metadata fixtures. Through UI pagination, the oldest
retained completed run was reopened and its exact export downloaded without
reupload or recalculation. Search then reopened the current-policy later report.

The resource audit checked NullPool sessions/engines, independent credential-read
sessions, DuckDB, shared bounded HTTP streams, atomic files, backup staging,
subprocess reaping and joined worker-only heartbeat threads. No Flask request
executor or unbounded result cache was introduced. Repeating **200 success and
200 exception** heartbeat/session/directory-scan cycles left handles at **262**
and threads at **7** after warmup; every helper was joined and the temporary
store removed. RSS stayed approximately 54.5–55.2 MiB; traced residual growth
was 2.6 KiB, including measurement records. Separate atomic-receipt checks ran
150 success and 150 failure cycles with 192 handles before/after and no temporary
files left. Blocking cancellation and real subprocess shutdown have dedicated
tests. This is finite repetition evidence, not a hosted soak test.

The pre-audit verification encountered one transient Windows temporary-file
sharing failure; its rerun passed and no cause was established. Current full
Windows/Linux suites pass. Live Fyers downloads, full broker/strategy startup,
production supervision and hosted durable storage remain separate external
checks. See [DATA.md](DATA.md), [DEVELOPMENT.md](DEVELOPMENT.md) and
[STORAGE.md](STORAGE.md) for supported operation and backup scope.
## Release preparation — 2026-09-06

The OpenAlgo release scope is single-owner self-hosting with official-price
research first. See [LAUNCH.md](LAUNCH.md) for the intended-user acceptance tasks.
This pass changes source-selection validation, installation/packaging tools and
documentation; calculation policy and saved numerical evidence are unchanged.

- Windows: **233 research checks passed, two skipped** because the host cannot
  create filesystem links without additional privilege (26.80 seconds).
- Linux: **240 passed, no skips** (50.02 seconds): the same 235 research cases
  plus five SQLite/eventlet cases, including both filesystem-link checks.
- The prior consumer UI pass has **43 frontend tests**. A fresh staged
  `npm ci` / production build also passes through the release packager.
- Missing, blank and invalid source choices create no source, job or artifact;
  explicit synthetic input retains its demo label. Existing explicit sources,
  authentication and CSRF continue to pass.
- The baseline installer copied only catalogued files into a new independent
  directory and verified **201 files / 39,100,537 bytes**. Original manifest
  hashes remain `80c5df101379250db495ae872474535251d2af8cdc22acb66c009f03c50a226d`
  and `8ff52f848ef4e65567f6a7cdf06c0e7b4d452454040f7acb77ed793b443fc51b`.
  Its network adapter is tested with controlled HTTP responses; the local
  provisioning check used the existing public bundle and did not refetch all
  original URLs. Missing/corrupt/oversized/redirect/timeout responses fail cleanly.
- Package tests check source privacy boundaries, exact hashes, current built
  assets, link rejection, repeatable fixture archive bytes, duplicate-output
  refusal and build failure/timeout cleanup. The package contains no live
  environment, runtime data or raw price archives. Source and compiled frontend
  are included together with licence and dependency lockfiles.
- Resource review covered context-managed input/output/HTTP streams, temporary
  staging directories, ZIP handles and waited build subprocesses. Exception and
  timeout tests exercise cleanup. This was a static and failure-path review;
  no new long-lived cache, thread or database session was introduced.

A clean native Linux smoke additionally runs **actual `app.py`, Gunicorn 25.3.0,
eventlet 0.41.2 and one web worker**, with the real native empty-installation
startup. There are no mocked application modules and no preview-auth identity.
All databases/configuration and ports are isolated, broker keys are blank, and
no existing strategy is loaded. Native admin setup, account login without a
broker, anonymous denial, CSRF rejection, current SPA, independent official
baseline, source preparation, external calculation and exact export pass.
Cooperative shutdown releases the lease; restarting the worker completes work
queued while it was offline and preserves the original export bytes.
After every owned process stopped, a consistent backup restored into a new
directory reproduced the same full export SHA-256. All three owned processes
(Gunicorn master and two successive research workers) exited successfully and
the isolated service ports were clear.

The machine-specific smoke receipt and source identities are under ignored
`.agent-native/native-staging/`. This check is loopback HTTP, not a deployed
HTTPS/proxy or systemd/container test. The Docker daemon was unavailable; its
Python lockfile correction was reviewed but no image-build success is claimed.
No external broker validation, public deployment, human trial or data/licence
clearance is implied. Earlier audit and capacity receipts above are preserved.

## Broker-first consumer correction — 2026-09-06

The user rejected the text-heavy interface and clarified that broker history is
the central product requirement. The official-price-first release assumption
above is superseded; its historical checks and candidate are retained as evidence
only. The user's tested run used NSE final CM bhavcopy archives, not Fyers.
No real broker download has been established by any check in this section.

The revised UI defaults new uploads to Fyers without silently selecting an older
public source. Existing reports show their actual provider. Results use
Summary/Trades/Settings tabs, six metrics and an equity chart; trades have six
useful columns, optional row details and 25-row pagination. Saved work moves to a
searchable dialog. Technical run receipts, raw counters and generic edge-case
panels no longer occupy ordinary screens. The exact export still contains the
complete saved evidence. Five main trade inputs lead setup, with advanced controls
and exact preflight available on request. Motion respects reduced-motion settings.

- Windows backend: **252 passed, two filesystem-link permission skips** (22.48s).
- Linux backend plus SQLite/eventlet: **259 passed, no skips** (52.38s).
- Frontend: **44 tests passed** across page, draft, preflight and layout suites;
  targeted Biome, TypeScript and production build pass. The preview build went to
  ignored storage; no tracked compiled assets were replaced.
- Readiness checks cover preview isolation, missing app configuration, absent,
  wrong or revoked broker authentication, symbol-master readiness and reference
  availability. They inspect native metadata without decrypting tokens or making
  broker calls. Ready-to-download means the request prerequisites are present;
  only a successful acquisition can prove data access.
- Resource audit reviewed the two short context-managed SQLAlchemy sessions in
  readiness. Success and exception tests check balanced connection checkout and
  return. No new persistent cache, thread or retained frame was added. This is
  static and failure-path verification, not a long-running production soak.
- Browser evidence captures the actual user's saved report before and after at
  controlled 1,000 and 390 CSS-pixel widths. Checks cover summary/trades/settings,
  arrow-key tab navigation, saved-dialog opening/Escape, source labels and native
  theme switching. These are scoped visual and interaction checks, not an
  accessibility certification. Machine-specific screenshots remain ignored.
- The user's saved export is byte-identical before and after the changes; its
  private receipt records both length and SHA-256. Calculation and data policies
  were not changed in this correction.

A separate real native app and research worker are available for user-owned
account setup and broker sign-in, using fresh isolated databases and ports.
Current frontend delivery, anonymous API denial, setup and worker startup pass.
The instance has no configured Fyers app credentials or broker session. Native
configuration and OAuth are still needed before the mandatory broker acquisition,
Historify persistence and saved broker-result acceptance checks can be completed.

## Native database and shared-broker scope correction — 2026-09-06

The user clarified that the product must use OpenAlgo's price database and its
existing broker integration, not a Fyers-specific research setup. New automatic
sources now read the installation's configured Historify archive first. Complete
stored coverage does not resolve broker credentials or import the symbol master.
Only missing reference-admissible dates call the current native broker. Returned
prices are saved through Historify and must match a read-back before calculation.
The optional stored-only source queues the same validation without downloading.
Development isolation is explicit; normal installations use their normal native
database rather than a mandatory second research archive.

The shared history service preserves richer adapter evidence where implemented
and otherwise accepts the existing native adapter's normalized history table.
Generic evidence explicitly states that raw broker transport was not captured.
No Fyers-only guard remains on new automatic sources, credentials or readiness.
Cached rows lacking original provider metadata remain labelled OpenAlgo Historify,
not the current broker. A retry can fill remaining dates through another broker
while retaining the exact earlier observations and source identities.

- **301 Windows tests passed, two filesystem-link permission skips** (25.76s).
- **308 Linux tests passed, no skips**, including SQLite/eventlet (52.63s).
- **49 frontend tests passed**, plus targeted Biome/Ruff, TypeScript and the
  production build. Setup says OpenAlgo prices; saved public/provider labels are
  retained. The running preview and separate native instance serve current assets.
- Fourteen archive-first tests use real temporary DuckDB storage: cache-only
  operation without credentials, missing-window downloads through three provider
  names, persistence mismatch, partial/empty response recovery, provider switching,
  known quarantine, duplicate-day replacement, bounds and failed-read cleanup.
- Three HTTP worker tests exercise CSV → queued source → real Historify → fixed
  backtest → exact saved export. The complete cache forbids credential/network
  access. Missing-date cases use generic Zerodha/Dhan handlers through the native
  history service, request only the missing date, persist/read back that price,
  and retain byte-identical exports after logout. Controlled responses are explicit.
- Twenty-six shared-history tests cover actual Zerodha normalization with
  controlled transport, eight native feed-token constructor variants, unsupported
  history/intervals, table bounds, cancellation and cleanup. The existing nine
  rich Fyers evidence tests and expiry/retry/restore pipeline continue to pass.
- Code inspection covered all 36 native data adapters. Thirty-one declare daily
  resolution, but Motilal is today-only, some adapters have no history, Tradejini
  is intraday-only, and crypto/sandbox declarations do not prove NSE equity
  availability. None of this is a live-account compatibility certification.
- Resource review covers short native DuckDB contexts, two short readiness SQL
  sessions, atomic receipt files, bounded aggregate cached rows/receipt bytes and
  request-local DataFrames. Failure-path tests verify cleanup. No new background
  thread/client/cache was introduced. Generic native calls retain adapter-owned
  in-flight timeout/retry behavior; checks around them cannot interrupt a hung
  adapter. This is scoped static and test evidence, not a production soak.
- The user's previously tested public-price export remains byte-identical. The
  calculation policy and prior saved evidence have not been changed or relabelled.

The pinned NSE daily identity/calendar/price reference remains a limitation of
current admission; this correction does not extend supported years, venues,
intervals or adjustment assumptions. Public prices never fill a missing native
candle. Real broker-account download and target-host operation are still open
acceptance gates. The isolated native test instance has no user account or broker
credentials configured; its setup, protected API denial and worker lease pass.


## Automatic minute acquisition verification, 2026-09-06

The isolated Windows targeted run passed 31 tests: 13 minute acquisition cases,
four native HTTP-to-worker minute workflows and 14 retained daily cache cases.
The HTTP cases use real temporary native DuckDB storage and fake-network generic
adapters for Fyers, Zerodha and Dhan. They verify cache completion without auth,
one missing minute despite an existing daily candle, exact persisted minute
values, `scanner-minute-causal-v1` saved export and unchanged reopen after broker
credentials are unavailable. The Fyers fixture verifies that a daily-only richer
evidence adapter falls back to the generic minute interface.

Additional cases cover duplicate/non-minute/out-of-daily-range quarantine,
expiry and remaining-data retry, persistence mismatch, missing grid slots,
explicit shortened sessions, receipt quotas and cancellation checkpoints. Ruff
passes for the changed acquisition/helper/test files. No real broker credentials,
network downloads, user databases or live strategies were used. This evidence
proves the controlled native workflow, not every broker's real minute retention
or a live authorized download. Daily reference identity and high/low consistency
are weaker than independently verified minute-tape provenance; that limitation
is retained in saved snapshots. The subsequent full Linux suite passed all 360 tests in 22.55 seconds in a
fresh isolated directory, including native app, supervision and symlink checks.
The owned native Gunicorn/eventlet instance restarted with configuration and all
11 existing database/artifact file signatures preserved. Its setup page returned
200 with account setup still required, protected APIs denied anonymous access,
and its external worker lease was fresh. No account or broker credentials were
created. A first Linux run failed solely because the isolated test runner supplied
a 30-character test pepper; correcting that runner to meet the native 32-character
minimum produced the clean full run. Final consumer asset verification is retained
in the ignored native refresh receipt.

The final frontend passes 55 tests, TypeScript project checking and the production
build. Browser checks at desktop and 390-pixel widths verify the intraday holding
controls and automatic source label. Obsolete daily-only setup narration was removed.
The automatic-preparation journey test changes the holding rules, receives a queued
preparation, switches to the completed minute source and resumes review without a
second upload. Seven backend admission/worker tests cover source reuse, wider
requirements, temporal search/sensitivity variants, minute checkpoint policy and
unchanged parent artifacts. The original saved export remains unchanged, with its
exact hash retained in the private verification receipt.

Resource audit for this correction was static review plus failure-path tests:
native DuckDB contexts close on success/error, receipt files use context managers,
generic history handlers close in `finally`, worker lease helpers are joined, and
chart instances are destroyed. Minute plans, rows, receipts and per-evaluation
state have explicit bounds. No new global cache or long-lived client was added;
this is not a production memory/descriptor soak or minute-scale capacity benchmark.

## Full main-app workflow verification, 2026-09-06

The owner authorized testing the existing installation and connected Fyers
account in place. The original date-only CSV was reused, with 361 signals across
298 symbols. The automatic planner kept daily prices for the ordinary backtest
and all daily search variants. Expanding holding periods from the original setup
to the full search increased required scope from 2,000 to 2,637 daily candles.
Existing matching-interval Historify rows were reused; only missing scope went
through OpenAlgo's ordinary connected-broker history service into that same
database. The completed expanded snapshot contains 2,606 native candles with
31 missing. Every calculation OHLC matches native storage exactly, with no extra
public-price adjustment. Original saved inputs and results remain unchanged.

| Main-installation workflow | Verified outcome |
| --- | --- |
| Original fixed daily repeat | Exact result equality with the previously completed original run; 200 closed, 149 skipped, 12 pending |
| Full optimizer | 648 distinct settings evaluated, zero remaining |
| Sensitivity | Double fees, higher slippage and reverse signal priority completed |
| Fixed later-period holdout | One later window and three sensitivity variants completed |
| Earlier selection plus walk-forward | Two later windows, 648 earlier settings per window and three sensitivity variants per window completed |
| Exact selected-setting rerun | Submitted through the real browser; configuration and entire result equal the saved optimizer selection |
| Representative intraday | One actual signal with an explicit intraday horizon automatically used 375 cached native minute candles; one closed trade, no missing prices |

The intraday run is a one-signal representative check, not a full intraday replay
of the 361-signal CSV or a live minute-download benchmark. Fyers is the actual
connected broker exercised here; other brokers remain controlled-adapter tests.
Missing daily candles retain pending outcomes and do not become fabricated
prices. The historical walk-forward checks used fresh capital for each window
and retained their original reports; private account performance is not reproduced
in these public verification notes.

Chrome checks covered automatic expansion/preparation, full optimizer submission,
selected-setting transfer and submission, saved-run search, reopening after a
full reload, trade filtering, saved settings, rendered charts and switching later
windows. All saved export payloads were serialized and hashed again from their
immutable inputs and reports with exact equality. The browser Export results
link was exercised, but final file arrival was not confirmed: browser policy
blocked download-manager inspection. This is a limit of that manual check,
not evidence of a failed export endpoint. Automated export/HTTP tests passed.

Full automated validation on the current source:

- Windows: **489 backend checks passed, two symbolic-link permission skips**,
  71.34 seconds; entire research suite plus shared history routing.
- Local Ubuntu/WSL: **504 backend checks passed, no skips**, 88.67 seconds;
  the same scope plus 13 SQLite/eventlet checks. Both Windows-skipped cases passed.
- Frontend: **1,478 tests across 75 files passed**, 27.54 seconds; TypeScript,
  targeted research/chart lint and production build also passed.

Automated runs used isolated temporary stores and existing local dependencies;
no new dependencies, infrastructure or system services were provisioned. No
application source change was needed during this full verification. Main-app
jobs used the existing external worker; no orders, strategy activation or mode
changes were performed. Exact job identities, source/export hashes and complete
runner logs are retained only under ignored `.agent-native/full-workflow/`.
The main app remains running on its existing local address.

Browser follow-up found a presentation issue in the representative intraday
report: its saved equity timeline contains 61,125 minute marks through the
snapshot cutoff although the one trade closed in January. The chart opens on the
latest September marks, showing a flat tail instead of the January trade. The
375 required price candles and saved calculation are correct, but the initial
chart viewport and unnecessary report size need a separate presentation fix.
The first walk-forward window also repeats its insufficient-sample label. These
findings prevent treating this run as a completed public-UI acceptance review.
A repository-wide whitespace check reported existing generated
`frontend/dist/index.html` line-ending whitespace; generated assets were not
rewritten during this test pass.

## Available-price report completion, 2026-09-07

The owner asked to proceed with the prices already present. Existing daily
backtest and optimizer results remain completed with the exact same native data
and pending outcomes; no new broker requests or calculation jobs were created.
A read-only recheck confirmed all nine prior jobs, exact baseline and optimizer
selection equality, and every daily/minute calculation price against Historify.

Settled minute reports now initially focus their chart on the actual entry/exit
span, including the preceding equity mark. This changes the viewport only:
the complete saved series, numerical summaries and exports are unchanged and
remain available. Pending outcomes and later-period reports retain their complete
review horizon. The recorded large minute export is deliberately unchanged.
Minute evidence rows use their timestamp identity instead of the repeated date;
the later-period view no longer repeats a sample finding already shown inside
the report.

The targeted three-file frontend suite passed 53 tests before the final
timestamp-key correction. The final research-page suite passed all 44 tests
without duplicate-key warnings; TypeScript and targeted lint passed. The main
installation's own production build passed, preserving its custom market-status
hook and environment hashes. Only the two reviewed UI source files were replaced;
source backups, the previous index, build log and receipt remain ignored under
the main installation's `.agent-native/available-prices-ui/2026-09-07/`.
Older asset files remain available for open tabs and rollback.

Resource audit: static review of the chart effect and its bounded viewport
object. The existing destroy-on-rerender/unmount cleanup still releases chart
subscriptions and canvas resources; no new listener, timer, client or cache is
introduced. No resource growth was suspected or measured. Chrome's normal login
session expired overnight, so this update has not yet been visually confirmed
inside the authenticated main-app report; no authentication bypass was used.

## Modular connector workflow, 2026-09-09

Real optional packages were installed only in disposable development/test Python
environments: VectorBT 0.28.5, Optuna 5.0.0, with existing core dependency versions
constrained. The source patch adds the locked `research` extra. No main account,
broker credentials, live archive, scheduled strategy or main process was changed.

Verification covered:

- 18 real VectorBT numerical cases: next-open timing, opening-equity sizing,
  shared cash, same-symbol lots, fees/slippage, protective orders, pending outcomes,
  sparse flat periods and early input bounds. The first native compilation needs
  a larger timeout; this allowance is scoped to the connector tests.
- 28 real Optuna cases: finite-grid parity, distinct bounded trials, composite
  trigger/trailing choices, deterministic restart, input/version binding, damaged
  evidence, cancellation and invalid scores. The optimizer receives the same
  immutable snapshot through the supplied engine callback.
- Native upload -> preflight -> queue -> real calculation -> reopen -> exact
  export for VectorBT fixed runs and Optuna with both VectorBT and the existing
  scanner. Additional checks cover owner access, unavailable packages, version
  drift and worker interruption/resume without repeating completed trials.
- Daily acquisition tests use real temporary Historify storage and controlled
  broker responses. Both engines reuse complete daily coverage without credentials;
  missing-only daily downloads persist and read back through the existing native
  service even with minute candles already present. No new live-broker download
  is claimed by these controlled checks.

The full Windows run recorded 548 passes, two existing symbolic-link permission
skips, and one incorrect new test assertion expecting HTTP 202 instead of the
existing Resume endpoint's HTTP 200. Linux recorded 563 passes and the same
assertion failure, with no skips; this includes 13 SQLite/eventlet checks. After
correcting only that assertion, its complete interruption/resume scenario passed
on both platforms. The verified case totals are therefore 549 Windows cases and
564 Linux cases, with the two Windows-only skips. The original full-run logs and
corrected Linux rerun remain under ignored `.agent-native/connector-work/`.

All 60 affected frontend tests passed, including supported controls, independent
optimizer choice, preserving strategy requirements, combined package availability
and retaining engine version pins for exact candidate reruns. TypeScript, scoped
Biome/Ruff checks, frozen-lock consistency and the production frontend build
passed. The build is under ignored `.agent-native/connector-work/frontend`; the
tracked distribution and main installation were not replaced. Browser visual
verification of the new controls in the authenticated main app is still pending.

Resource audit: static review found a fixed-size adapter registry, bounded
500,000-cell engine matrices, bounded serial Optuna studies and one retained full
winner report. New code opens no broker client or separate database. Persistence
uses the existing context-managed sessions and atomic/chunked artifact writer.
The worker's lease helper is joined on success, failure and interruption; lifecycle
tests assert its thread is gone after both failure and resume. Cancellation cannot
interrupt an upstream import/compilation immediately, but prevents publication
after it returns. No sustained descriptor/RSS benchmark or production-speed claim
was made. That early checkpoint preceded the joint portfolio, Nautilus and
later-period implementation verified above. Margin remains outside the declared
connector scope.

## Main-installation connector test, 2026-09-09

The owner asked to open and test the feature in the existing app. Only connector
source, optional dependency declarations and the main app's own rebuilt frontend
were applied, with reversible backups under its ignored `.agent-native/` directory.
All previously installed package versions, `.env`, the custom market-status hook
and the original full historical result were verified unchanged. Existing strategy
and workflow schedules were empty before restarting the owned web/worker launcher.

Five actual scanner observations with complete stored holding windows were selected
from the original upload for a small functionality test. The normal native price
preparation completed from 40 stored daily candles; every OHLC value matched native
Historify, all acquisition receipts were archive reads, and no new broker requests
were needed. The ordinary worker completed VectorBT with five accepted/closed
trades and zero pending/skipped, and VectorBT/Optuna completed 25 distinct settings
from an 81-setting grid. This is a workflow check on a selected small sample, not
strategy-performance evidence or a new live-broker download test. Exact private
receipts and the reusable CSV remain inside the owner's installation.

The main installation passed all 60 affected frontend tests and its TypeScript /
production build. Chrome showed the existing authenticated account, saved test
source and Calculation tools control. The app remains available on its usual
loopback port 5000. Broader connector numerical, recovery and Linux checks remain
as recorded above; they were not repeated solely for this installation change.
# Distribution and connector defaults — 2026-09-09

This increment packages the first connector workflow as one native OpenAlgo
Research distribution. It does not add new simulation scope or change either
engine's financial policy.

- **69 affected frontend checks passed**, including new VectorBT/Optuna defaults,
  unavailable-engine blocking, fresh New run behavior, legacy draft recovery and
  exact saved-candidate settings. TypeScript and targeted Biome checks passed;
  the final small connector label adjustment also passed its ten focused tests.
- **31 distribution/compatibility checks passed on Windows**, with one symlink
  test skipped because the host does not permit creating those links. Checks
  include a missing native price adapter, incompatible host call signature,
  unsupported package/Python versions, archive hashes, source mutation during
  build, private-data exclusions and subprocess failure cleanup.
- **11 production supervisor checks passed on Linux**: signal shutdown with a
  checkpoint, startup failure, child crash/exit, stubborn children, descendant
  cleanup and 20 restart/exit cycles with unchanged descriptor and child counts.
  Windows passed the command contract; ten POSIX lifecycle tests skip there.
- The real source builder ran locked `npm ci` and a TypeScript/production frontend
  build. The extracted ZIP passed its source and installed-package compatibility
  check and **70 real calculation/API/native-data workflow tests**, covering
  VectorBT, Optuna, cancellation/checkpoint recovery and daily Historify behavior.
  All shipped file hashes matched the release manifest. These tests used isolated
  temporary databases and controlled broker responses, not an owner's account.
- Targeted Ruff, shell syntax, Compose configuration and whitespace checks passed.
  The added CI workflow specifies both supported operating systems and an image
  build/dependency check; it has not run remotely in this local work.

The actual Docker image build/start, first account setup and populated-volume
update/restore are **not verified here**: Docker Desktop's Linux daemon was not
running. No live app, broker, credentials or account database was used. The owner's
running main installation was not modified by this increment. No public repository
was created and nothing was deployed.

Resource audit: the checker uses short, closed file reads and no app/database or
numerical imports. Packaging owns and closes staged files and archives, reaps
build children and cleans staging on failure. The supervisor retains at most three
child handles, restores signal handlers and removes its temporary stop-file
directory on all ordinary exit paths; Docker's tini reaps orphan descendants.
File ownership was reviewed statically; process cleanup was also measured in the
Linux repeated-cycle test.

Local receipts: `.agent-native/distribution-verification/receipt.json`,
`packaged-workflow.xml` and `check-*.log`; release source/asset manifests and build
logs live under `.agent-native/release/`. These are ignored development evidence.
