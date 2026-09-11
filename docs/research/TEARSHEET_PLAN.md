# Full research analysis: implementation plan

**Next presentation increment:** [Report experience and study journey](REPORT_EXPERIENCE_PLAN.md)
specifies the QuantStats-inspired continuous report, Optuna dashboard, candidate
links, comparisons and validation. That work is planned; the implemented foundation
and its historical acceptance below remain separate.

Implemented 11 September 2026. This expands the native Research reporting layer while
retaining VectorBT/Nautilus simulation and Optuna optimization. The extension is
outside this work. Acceptance and applicable capability limits are recorded below.

## User journey

1. Run or optimize a portfolio using the existing OpenAlgo price plan.
2. Keep the compact Summary, Trades and Settings views. Add **Tear sheet** for
   complete performance statistics and interactive charts. Group statistics by
   returns, risk, trades, account and engine; provide search and concise definitions.
3. Every newly evaluated trial retains its full scalar statistics. The existing
   Columns dialog can select those metrics, and trial settings also offer its
   recorded statistics. Do not copy the winner's equity or ledger into other rows.
4. **Study analysis** exposes Optuna's native visualizations with meaningful axis
   selection: progress, importance, parameter slices, contours, parallel coordinates,
   rank, objective distribution and timeline. Show requirements for analyses not
   applicable to this study, rather than fabricating observations or objectives.
5. Existing studies keep their original immutable result. A separate analysis
   action can derive a versioned report from their saved evidence without broker
   downloads. Old trials lacking full curves retain only supportable statistics;
   obtaining missing trial evidence requires an explicitly requested replay.
6. Save analysis alongside the research job and make it exportable. Refreshing or
   reopening shows saved analysis without rerunning calculations.

## Capability coverage

| Provider | Statistics and visuals to cover | Conditions |
| --- | --- | --- |
| VectorBT 0.28.5 | Full applicable Portfolio/Returns/Trades/Drawdowns statistics; account value, cash, exposure, cumulative returns, underwater/drawdowns, trade P&L and duration, returns distribution, monthly/yearly returns, rolling risk/return views | Preserve shared-account aggregation; distinguish marked account returns from closed-trade statistics; do not average independently funded lot statistics into the shared account |
| NautilusTrader 1.231.0 | Native P&L, returns and general statistic registries; orders, fills, positions and account reporting; equity, drawdown, monthly/yearly returns, return distribution, rolling Sharpe and bars with fills where available | Inspect the pinned runtime API rather than assuming latest documentation matches it; retain native meanings and modeled-OHLC timestamp qualifications |
| Optuna 5.0.0 | History, EDF, importance, slice, contour, parallel coordinate, rank, timeline; capability entries for intermediate values, Pareto front, hypervolume and terminator improvement | Intermediate values require recorded steps; Pareto/hypervolume require real multi-objective studies; terminator plots require their own evaluation evidence/dependencies. A return/drawdown scatter must not be labeled a native multi-objective Pareto study |

Sources: [VectorBT Portfolio](https://vectorbt.dev/api/portfolio/base/),
[VectorBT returns](https://vectorbt.dev/api/returns/accessors/),
[Nautilus reports](https://nautilustrader.io/docs/latest/concepts/reports/),
[Nautilus visualizations](https://nautilustrader.io/docs/latest/concepts/visualization/),
[Optuna 5 visualization API](https://optuna.readthedocs.io/en/v5.0.0/reference/visualization/index.html).
The installed version is the authority for callable native APIs.

## Scientific and storage rules

- Preserve existing summary values, execution rules, winner ranking and original
  evidence. Add a versioned analysis object, never silently redefine old fields.
- Record return sampling frequency, annualization, risk-free/required-return
  assumptions, duration units, native provider and undefined-value reasons.
  Minute inputs must not treat each minute as a trading day. Keep closed-position
  return statistics distinct from marked-account statistics.
- Unsupported benchmark-relative statistics require an actual aligned benchmark;
  no arbitrary index prices or duplicated-signal-lot pseudo-benchmark.
- Native NaN/infinity become JSON null plus an explanation; zero stays zero.
  Loss-free profit factor and constant-return Sharpe must not become invented zeros.
- Store bounded scalar values per trial and a shared catalog once. Detailed
  curves/reports belong to retained complete results. Plots are generated outside
  the broker-facing web process and use bundled Plotly, never remote executable HTML.
- Reconstruct old Optuna studies read-only from recorded distributions and all
  proposals, including reused/pruned proposals. Do not run the sampler or optimize
  again to draw a chart. Do not invent historical trial timestamps.
- Separate versioned analysis artifacts allow new report calculations without
  changing exact replay identity or the older saved result hash.

## Delivery and acceptance

### Analysis contract

`PortfolioResult.analysis` is optional for older evidence. Version 1 has
`version: "research-analysis-v1"`, `basis: string[]`, `catalog: MetricDefinition[]`,
`metrics: Record<string, number | string | null>`,
`unavailable: Record<string, string>`, and `charts: AnalysisChart[]`.
Each metric definition contains `key`, `label`, `group`, `format` (percent, money,
number, text), `description`, and `source`. Percent values are percentage points.
Chart entries contain `id`, `title`, `status` (available or unavailable), optional
`reason`, and optional Plotly `figure: {data, layout}`. Figures contain JSON only.
Native metrics use provider-prefixed keys to retain their distinct meanings.
Common account metrics use `account_` keys.

`PortfolioTrial.analysis` stores only `version`, `metrics`, and `unavailable`.
The catalog is stored once in `experiment.analysis_catalog`. Full report analysis
remains on retained reports. `experiment.study_analysis` has `version`, `basis`,
and `charts`, using the same chart contract. Chart axes can use native Plotly
interaction and frontend parameter controls.

An older saved result is enriched through a separate saved analysis artifact;
the API overlays it for presentation. Original results and exact replay inputs
are never rewritten. Such analysis derives only from retained evidence and marks
missing native-only statistics as requiring a new backtest.

1. Inspect pinned APIs and define a versioned statistics/catalog/figure contract.
2. Collect native engine statistics and compatible account charts; test daily,
   minute, open-position, no-trade, all-win, constant-return and multiple-strategy cases.
3. Persist scalar analysis for each new optimizer configuration; retain exact
   checkpoint and replay behavior. Add native Optuna study figures and requirement metadata.
4. Add bounded background analysis for old saved reports; test ownership,
   cancellation/failure, repeated requests, restore/export and unchanged original evidence.
5. Implement native tear-sheet/study tabs, searchable statistics, trial-column
   integration and accessible interactive plots with small empty states.
6. Run numerical/provider comparisons, integration and UI tests, resource review,
   representative performance checks and browser acceptance. Install reviewed changes
   into the user's normal app without interrupting active calculations.

## Implemented coverage and acceptance

- VectorBT: 27 shared-account statistics plus its complete 28 Portfolio, 20
  Returns, 25 Trades and 21 Drawdowns registry entries (121 total).
- Nautilus: all 34 native statistic classes, expanded into 61 scalar entries
  where account returns and closed-position returns require separate bases,
  plus 27 shared-account statistics (88 total). Native account events and saved
  orders/fills/positions are inspectable.
- Optuna: history, importance (seeded fANOVA), slices, contours, parallel
  coordinates, rank and EDF use native Plotly APIs reconstructed from all
  recorded proposals. New studies record real trial timings for native timeline.
  Two parameter selectors update saved plots without resampling or simulation.
- Portfolio visuals include equity/cash, exposure, cumulative return, underwater,
  daily distribution, monthly/yearly returns, rolling Sharpe/volatility, trade
  P&L/duration, native record tables, and selected-symbol frozen OHLC with fills.
  Validation charts use their own report period and symbol scope.
- Every new distinct optimizer configuration retains its full scalar metrics.
  Per-trial storage omits repeated catalogs and figures; complete retained
  reports carry the catalog and charts. Checkpoint replay preserves scores,
  proposal identity and recorded timing metadata.
- Old analysis runs through the existing bounded worker/queue and stores a
  separate content-addressed artifact. Original result/export/replay evidence
  remains unchanged. Reopening overlays saved analysis; no broker access occurs.
  Native-only values that were never recorded are not reconstructed by guessing.

Undefined metrics remain null with a concise reason. Benchmark-relative metrics
need a genuinely aligned benchmark. Intermediate-value plots require recorded
trial steps; Pareto/hypervolume require multiple objectives (and a reference point
for hypervolume); terminator analysis requires recorded error estimates. The
current single-objective full-portfolio workflow intentionally records none of
those additional observations. These requirements appear only when that analysis
is selected. No extra optimization mode or invented metric was introduced.

Verification includes native provider comparisons; EOD/minute annualization;
initial-to-first-close return; shared cash; no/open/all-winning trades; constant
returns; ownership/admission; cancellation/retry; immutable evidence; complete
checkpoint resume; chart bounds and validation-period isolation. Both adapter
regression suites pass with analysis attached. Source compatibility, packaging
and storage tests pass. UI tests cover dialogs, dynamic columns, analysis
preparation/polling, period routing, native plots and original-result preservation.

Resource review: engine objects remain within their existing disposal scope;
analysis owns no persistent cache or connection. Saved analysis uses contextual
DB sessions and the existing joined worker heartbeat monitor. Native charts are
bounded to 1,200 observations (200 rows in plotted native tables), while scalar
metrics use all observations. The searchable native-record view retains access
to complete saved records. On the controlled fixture, warm saved VectorBT
analysis averaged 0.049 seconds across ten calls; this is not an end-to-end
runtime promise for a large portfolio or a cold runtime.

The increment was installed into the user's existing OpenAlgo on 11 September,
with zero active calculations interrupted. Existing configuration, broker
adapters and research metadata were verified unchanged at installation. The
user's saved study successfully prepared additional statistics and native Optuna
figures from its retained evidence. Browser acceptance also covers the chart
labels, themes, price/parameter selection, column chooser and reopening.

Recorded checks: 63 frontend tests plus TypeScript, scoped Biome and the native
production build; 46 Windows native/analytics checks and 46 Nautilus WSL checks
(other-provider cases skipped); 55 distribution/storage/package checks (one
platform skip); targeted saved-analysis, Optuna reconstruction/replay and native
portfolio HTTP workflow checks. The saved study displayed 94 available and 27
unavailable catalog values from its older evidence; this is distinct from full
metric collection on a new engine run. The initial native saved-analysis job
finished in about 24 seconds with cold imports; a subsequent symbol-only update
finished in about one second using retained analysis and frozen prices.
