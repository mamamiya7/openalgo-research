# Native full-procedure calibration — 21 October 2026

## Implemented scope

This extends [controlled-account qualification](SEARCH_CALIBRATION_PROTOCOL.md)
with actual VectorBT calculations on generated markets, genuine constraint audits
and alternate sampler streams across the entire v15 procedure. It is offline
developer qualification, not a consumer setting or a synthetic stage in ordinary
optimisation. Production defaults, budgets, acquisition and saved numerical
contracts are unchanged. There are no new dependencies, database tables or UI.

`research/native_calibration.py` groups observations by independent dataset.
`research/procedure_seed_calibration.py` freezes three bounded sampler programmes.
The explicit repository runner, `test/research/native_calibration_runner.py`, uses
the actual chronological controller, native causal gates, constrained VectorBT
accounts, real audits and sealed development histories. It does not fabricate
returns, ledgers, completed-trade support or supported audit results. Its audit
observer invokes the genuine consumer without retaining mock call histories.

## Generated markets

Null models use symmetric unpredictable opening jumps and otherwise flat bars;
signals are fixed before prices. This avoids inventing intrabar paths or favourable
stop/target ordering from OHLC extrema. Zero conditional drift is a declared
martingale-model assumption, not a description of real markets.

| Stratum | Declared model |
|---|---|
| Independent null | Separate symbol shock streams with zero conditional drift. |
| Correlated null | Common and symbol-specific shocks without a mean advantage. |
| Volatility-shift null | Calendar-fixed variance change without a mean advantage. |
| Sparse-execution null | Valid scanner chronology but nominal prices above capital, so no whole-share fills. This tests missing execution support, not sparse scanner-date admission. |
| Positive control | Positive development drift and adverse final-period drift; final losses cannot select a different development winner. |

Generator identity and independent data seeds are recorded. Prefixes do not
resample randomness or depend on future requested length. Synthetic volume has
unknown broker units: reviewed-broker participation is disabled, while actual
correlation, ATR, turnover, stop-risk and other admitted constraints apply.
This suite does not qualify real broker downloads, quantity profiles, corporate
actions, executable liquidity, minute execution or Nautilus parity.

## Whole-procedure sampler checks

| Programme | fit1 | fit2 | fit3 | search | repeat 1 | repeat 2 |
|---|---:|---:|---:|---:|---:|---:|
| Canonical 0 | 0 | 0 | 0 | 0 | 1 | 2 |
| Alternate 1 | 101 | 211 | 307 | 401 | 409 | 419 |
| Alternate 2 | 1009 | 1013 | 1019 | 1021 | 1031 | 1033 |

The internal `calibration_seed_plan` is not wired to user requests. Its sealed
actual seed map binds the checkpoint, optimizer and development history identity.
All six samplers use those actual seeds. Logical final-repeat indices 0/1/2 are
distinguished from RNG seeds. Default `None` retains existing structures and
proposals; explicit canonical context has the same proposals but its own offline
identity. Recovery rejects a different context or ordinary-production masquerade.

Each programme pauses after a committed first-fit proposal and resumes with its
retained native accounts. Completed account evidence must not be recalculated or
replaced. The canonical programme remains primary. Alternatives report sensitivity;
they cannot select a luckier replacement. Decision agreement and exact settings
agreement are recorded separately. Final outcomes remain excluded from selection.

The frozen declaration includes source/runner identities, datasets, programmes,
budgets and acceptance rules. Every error or partial programme remains recorded.
Missing audits, incomplete fits, changed datasets, mismatched seeds or unsupported
recovery make a dataset incomplete, never a safe rejection.

The null event is **any changed recommendation across the dataset's programmes**.
Acceptance requires zero such events and at least one positive-control dataset
supporting changed settings in all three programmes. Each dataset counts once;
programmes, trades and proposals are not independent replicates. Models are not
pooled. With only one null dataset per model, zero events still yield a 95%
one-sided 95% upper bound. This is engineering qualification, not general FDR
control, live profit confidence or independent real-market replication. Larger
independent suites and declared precision targets remain future work.

## Bounds and exact reuse

Each programme keeps v15's maximum 408 proposals and 512 calculation attempts;
earlier rejection can skip repeats and finish at 272 proposals. Existing history
mark/byte limits and checkpoint bounds remain. A dataset-scoped native account
cache may share pristine real reports across programmes. Keys include exact
conditioned strategies, signals, snapshot/provenance and capital. Reports are
copied before enrichment and on hits; samplers, search state and audits are not
reused. New calculations and account reuses are counted separately in sealed facts.

The cache rejects another dataset or reopening after close, clears on success
and exception, and is bounded to 512 entries and 64 MiB of encoded report bytes.
That is not a hard Python RSS cap; object overhead and process memory are measured
separately. Parallel qualification uses bounded separate dataset processes. It
does not add an executor or persistent cache to the ordinary application.

## Developer command and retained evidence

Use the source checkout and existing pinned development environment. Consumers
still install one OpenAlgo Research distribution, without a separate calibration
app. Disable normal dotenv loading and use isolated fixture settings. From the
repository root, choose a fresh ignored output path:

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:PYTHONPATH = '.;test/research'
$env:API_KEY_PEPPER = ('0' * 64)
$env:APP_KEY = 'isolated-native-calibration'
$env:DATABASE_URL = 'sqlite:///.agent-native/native-calibration/isolated.db'
$env:LOG_DIR = '.agent-native/native-calibration/logs'
$env:LOG_FORMAT = '%(levelname)s %(name)s %(message)s'
$calibrationCode = git rev-parse HEAD
python tools/research_calibration.py `
  --runner native_calibration_runner:run_case `
  --runner-identity full-v15-native-synthetic-opening-jump-markets-v1 `
  --code-identity $calibrationCode `
  --execution-model native_synthetic_market `
  --whole-procedure-seeds --replicates 1 `
  --output .agent-native/native-calibration/receipt.json
```

This declares five datasets and fifteen substantial full programmes. The CLI runs
serial callbacks with fresh caches. A reviewed parallel driver can share one
bounded cache across each dataset's programmes while preserving the declaration.
Use a reviewed source digest for modified code; do not label a dirty working tree
with a clean commit identity. The CLI freezes `receipt.json.plan.json` before
runner import, retains individual outcomes in `receipt.json.programmes/`, and
never overwrites earlier or interrupted evidence. A retry needs a new path.

## Qualification and activation

The frozen native suite is in progress. Final coverage, resource measurements and
activation facts will be recorded after complete qualification. Feasibility
diagnostics and component checks alone do not constitute a passed suite.
