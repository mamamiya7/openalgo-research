# Offline search calibration protocol

This is an engineering qualification of the complete declared recommendation
procedure under controlled models. It is not a probability of profit, a live-data
p-value, or general false-discovery-rate control. Existing descriptive block
ranges remain descriptive. Repeating sampler seeds on the same market history
does not create independent datasets.

Freeze the recipe, code identity, explicit runner identity, sampler seeds and all
model cases before looking at their results. The five model strata are independent
zero-edge account outcomes, correlated zero-edge outcomes, a changed-distribution
zero-edge case, sparse zero-edge opportunities and a persistent-edge positive
control. Each runner must document its actual generator and null assumptions;
scenario names alone do not establish those assumptions. Data seeds are distinct
from sampler seeds. The runner must invoke the complete controller and retain
the actual evidence identity for each case, including unchanged-reference choices.

The runner declaration distinguishes a controlled account-economics model from
native execution over synthetic market prices. Model accounting and mocked
constraint audits do not qualify real native execution. Native fill, capacity,
causality and recovery tests are separate complementary evidence. Neither path
proves a fresh broker download or future execution quality.

The included `test/research/calibration_runner.py` invokes the actual v15
controller, sequential Optuna searches and native causal indicator gates. It
retains real development-evidence shards in bounded memory and checks the frozen
development choice before deliberately adverse final outcomes. Account marks
are controlled model outcomes; the native constraint audit is an explicit stub.
This qualifies orchestration under that model, rather than native fill economics
or the correctness of broker prices. Durable service storage/restart and genuine
native execution are qualified separately.

Runner v2 supplies canonical audit-interface strings so the actual sensitivity
consumer is exercised. A sealed `test_double` declaration explicitly records
`kind: controlled_account_model`, `native_execution: false` and the orchestration
purpose. Those canonical interface strings are test-double inputs, not evidence
that native constraints or fills were evaluated. Before an expensive suite,
run the fast consumer-contract tests: they invoke the actual sensitivity audit
consumer and reject incompatible scope, qualification, feature identity or seals.
An earlier v1 runner used incompatible interface strings and failed its positive
control. Its failed receipt is retained and must not qualify a release or be
counted as evidence of successful false-recommendation control.

Its market-price, source-signal and causal-gate fixture is fixed across cases.
Independent data seeds generate independent **controlled account-outcome
datasets conditional on that shared fixture**, not independent raw-price
histories or market regimes. The independent-null model uses separate numerical
settings innovation streams. Hypotheses sharing identical numerical settings
share the same potential outcome stream; native gates still determine actual
support. The correlated-null model shares 85% of innovation variance across
settings. The shifted-null model changes variance, without assigning any setting
a predictive mean advantage. Every null setting has the same expected daily
return; the sparse model additionally limits support to at most four actually
admitted entry dates. The positive control deliberately gives changed numerical
settings a persistent advantage to test the complete recommendation branch.
This is an artificial power check, not an alpha model. Cost stress preserves
each settings' underlying potential outcomes and subtracts additional costs.
No observed winner determines those generated potential outcomes.

One observation is one completed programme on one independently generated dataset.
Trades, trial proposals, correlated candidates and repeated sampler seeds are not
the denominator. An ordinary conservative controller branch that deliberately
skips secondary seed searches is complete; an interrupted or truncated programme
is not. Errors, missing cases, incompatible sampler declarations and incomplete
histories make the suite incomplete and suppress its binomial bounds. They are
never counted as safe rejections. Every declared case remains in the receipt.

Qualification requires all declared cases complete, zero changed recommendations
in the controlled null cases and at least one changed recommendation in the
positive control. The latter prevents an always-reference implementation from
passing. These are frozen engineering acceptance rules, not a calibrated
statistical rejection threshold. Do not retune thresholds to a failed receipt or
rerun only successful data seeds.

Per null stratum, report changed recommendations, completed independent runs and
the observed rate. The exact one-sided 95% Clopper-Pearson binomial upper bound
describes repeated independent datasets under that same specified model only.
For zero events the bound is `1 - 0.05 ** (1 / n)`: zero events in 20 independent
runs still permits about 13.9%. Do not pool distinct null models into a universal
bound. Correlation within an account/candidate universe is allowed by an explicit
generator; dependence between replicate datasets invalidates the IID binomial
interpretation. The current bounded suite allows 1–32 replicates per stratum and
does not establish a 5% false-recommendation guarantee.

`research.search_calibration.compile_plan` creates the sealed declaration.
`run(plan, callback)` executes each callback as `callback(case, plan)` and records
compact outcomes. `verify_receipt` recomputes coverage, strata and bounds. Seals
detect changed retained facts; they are not cryptographic signatures, independent
audits or proof that a declared runner executed honestly.

The CLI `tools/research_calibration.py` requires an explicitly named local Python
runner (`--runner module:callback`), code/runner identities, execution model and a
new output path. It does not import arbitrary result JSON and promote it to
qualified native execution. Run it only with isolated synthetic data, without
normal broker credentials or databases. The tool creates a new receipt and never
overwrites earlier evidence. A suite receipt is not attached to a user's saved
study as calibrated strategy confidence.

For a developer qualification from the repository root, with the development
dependencies installed, use a new ignored output path. The explicit runner
identity is `full-v15-optuna-native-gates-controlled-account-model-v2`:

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:PYTHONPATH = '.;test/research'
$env:API_KEY_PEPPER = ('0' * 64)
$env:APP_KEY = 'isolated-calibration-fixture'
$env:DATABASE_URL = 'sqlite:///.agent-native/calibration/isolated.db'
$env:LOG_DIR = '.agent-native/calibration/logs'
$env:LOG_FORMAT = '%(levelname)s %(name)s %(message)s'
$calibrationCode = git rev-parse HEAD
python -m pytest test/research/test_search_calibration.py --confcutdir=test/research -q
python tools/research_calibration.py `
  --runner calibration_runner:run_case `
  --runner-identity full-v15-optuna-native-gates-controlled-account-model-v2 `
  --code-identity $calibrationCode `
  --execution-model controlled_account_model `
  --replicates 4 `
  --output .agent-native/calibration/receipt.json
```

Use a reviewed source digest instead of the commit identity when qualifying
uncommitted changes; a clean-commit label must not misrepresent modified source.
These fixture-only environment values are not normal account credentials. The
runner does not read a normal installation's data or place orders. Each complete
case is substantial work: the default declares 20 full procedure invocations,
with 272 proposals normally and up to 408 when repeated search checks run.
The screening count is frozen from applicable clock/execution probes before
scores: it is at most 50, not always 50. Each search therefore has exactly
`68 - screening_count` adaptive proposals, at least 18. A smaller daily screening
programme must not be rejected for having more than 18 adaptive proposals.

If interrupted, retain its plan and partial diagnostics as incomplete evidence.
A newly qualified run needs complete coverage of its own frozen declaration;
discarding interrupted/failed cases cannot make an earlier suite pass. Parallel
qualification, when used, must isolate controller instances in separate processes
and preserve the same predeclared cases, runner/model scope and full denominator.

The runner's completed outcome contains `case_id`, `baseline_config_id`,
`selected_config_id`, `proposal_count`, `account_count`, planned `sampler_seeds`,
`seed_checks_status` (`completed` or deliberately `not_run`),
`procedure_completed`, `development_selection_frozen`,
`final_excluded_from_selection` and `evidence_id`. The three boolean facts must be
true. With the v15 default, proposal coverage is 408 with repeated seed checks or
272 when they are deliberately skipped. A failed/partial callback returns its
case ID and `status: error` or `incomplete`. A completed outcome returns
`status: completed`. Final-period outcomes never decide the recorded development
recommendation or rescue a rejected search.

Primary references: [Optuna's sampler and reproducibility documentation](https://optuna.readthedocs.io/en/stable/faq.html#how-can-i-obtain-reproducible-optimization-results)
explains deterministic sequential seeded sampling; [Bailey et al., The
Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
explains why strategy selection must be included when assessing overfitting.
This protocol does not implement that paper's CSCV estimator or claim its
probability estimates.
