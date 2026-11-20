# OpenAlgo Research

**Backtest and optimize CSV signal strategies inside OpenAlgo.** One install,
one account, one broker connection, and the same Historify price archive.

This is a native distribution fork of [OpenAlgo](https://github.com/marketcalls/openalgo),
with a React interface and connectors to VectorBT, Optuna, and optional
NautilusTrader. Research manages the inputs, data preparation and results;
the connected engines perform the calculations.

Inspired by **Chartink scanner signals**: evaluate a scanner's CSV signals as a
portfolio, with your own stops, targets, trailing rules and holding periods.
The aim is to connect broker data, backtesting and optimization in one open-source
workflow. Start directly from a Chartink scanner with the Chrome extension, upload
a CSV export, or reuse saved signals.

**Current release:** preview.39 / OpenAlgo 2.0.3.4, including diagnosed AI decisions and repeated-failure screening. [Changes and reuse](docs/research/releases/0.1.0-preview.39.md).

**Published download: `0.1.0-preview.39` · OpenAlgo 2.0.3.4 · Windows and Linux**

This release packages [preview.39](docs/research/releases/0.1.0-preview.39.md): OpenAlgo 2.0.3.4, diagnosed automatic research, funding recovery, repeated-failure screening, exact-setting validation and saved-result reopening.

[**Download OpenAlgo Research**](https://github.com/mamamiya7/openalgo-research/releases/download/research-v0.1.0-preview.39/openalgo-research-0.1.0-preview.39.zip)
· [Install and start](docs/research/RUNTIME.md)
· [Published release notes](docs/research/releases/0.1.0-preview.39.md)
· [Architecture](docs/research/SYSTEM_MAP.md)
· [Released source](https://github.com/mamamiya7/openalgo-research/tree/main)

## What’s in this branch

- **Recorded automatic decisions.** New runs learn feasibility constraints, challenge higher costs and nearby settings, diagnose capital blockers, and preserve the verdict when saving and reopening. See [preview.30](docs/research/releases/0.1.0-preview.30.md) for scope and qualification.

- **Automatic sizing and capital estimates.** New automatic runs explore sizing within fixed risk limits, reject sparse or concentrated historical improvements, and check estimated starting capital against the selected rules. The full 408-proposal search and saved evidence remain intact. Historical findings do not imply independent validation.

Upload a signal CSV or send a Chartink history, review your capital and trading
rules, then choose **Backtest** or **Optimize → Automatic research**. The app
selects the required prices, applicable research methods and validation. Existing
Historify prices are reused; only missing required coverage comes from your broker.
Date-only daily strategies stay on daily candles.

- **Start downloads while reviewing Chartink settings.** Opening the native setup starts background price and input preparation. Matching daily fixed and v16 automatic runs reuse checked prepared inputs. No backtest starts until Run.
- **Faster preparation for large scanners.** Reuse overlapping return windows,
  exact correlation calculations, checked volume observations and bounded stock
  features. Fully cached stock records share bounded, durable disk publication;
  every candle, source, calendar and saved recovery reference remains checked.
- **Fixed backtests from saved automatic setups.** Both the data preview and
  saved-library launch retain the matching versioned execution policy and account
  limits when switching to Backtest. Original strategy rules stay unchanged.
- **146-stock acceptance under a minute.** The installed Windows app completed
  the cached-price fixed-backtest journey from opening through saved result in
  **58.859 seconds**, with identical financial evidence. Large receipt size,
  hashing and seal checks now use bounded canonical pieces. Save, reload and
  reopen were verified. Full automatic research still allows 408 proposals/512
  accounts and requires adequate history; this timing is for the fixed backtest.
- **Current native OpenAlgo.** Platform 2.0.3.3 with reviewed official updates through
  17 November, charts 2.7.3, broker configuration and dependency security fixes.
  Saved research keeps its frozen prices, markers and replay.
- **Faster cached loading.** Save stock-preparation progress in bounded groups of
  16 fully cached stocks, while continuing to validate each candle and update
  visible progress. Broker responses still save individually.
- **Less work per trial.** Hash and publish one owned checkpoint snapshot, and
  check cancellation through fresh reads on a scoped connection. Matched short
  native-worker comparisons took about 24% less new-trial time with identical
  scientific checkpoints; this is not a new whole-run timing.
- **Faster recovery.** Reuse content-matched input validation within each job, skip
  unchanged saved-fit publications and avoid unnecessary replay writes.
- **Faster cached runs.** Avoid repeated storage-folder scans during calculations,
  redundant fully cached checkpoints and per-bar pause writes, while retaining
  cancellation, ownership and strict evidence-publication checks. Fresh Fyers daily/minute
  acquisition and exact Historify reuse were verified in Chrome.
- **Faster stock preparation.** Overlap two missing daily warm-up windows through
  the native rate limiter; reuse existing history and day-local calculations.
  Required lookbacks, provenance and portfolio checks stay intact.
- **Visible automatic progress.** See committed development returns, drawdown,
  trade counts, measured pace and recorded check outcomes while research runs.
  Interim winners are provisional. Previously used dates can be explicitly run
  as historical optimisation, clearly labelled as not independently validated.
- **One automatic research action.** Compare supported stock/market conditions,
  timing, ATR/percentage exits, trailing rules, sizing and causal portfolio
  allocation. Capital and risk limits stay fixed; detailed controls are optional.
- **A conclusion you can inspect.** Compare the original setup with proposed
  settings, later-window checks, costs and stability. Weak evidence keeps the
  original. This is a bounded historical search, not a universal optimum.
- **Market suitability from saved prices.** See performance and sample sizes by
  recorded market condition, with development and final-period findings separate.
  This reporting adds no downloads, simulations or optimisation rules.
- **Reliable automatic runs and recovery.** Fixes interruptions when starting,
  resuming and recording portfolio-allocation proposals, plus the saved-study
  analysis handoff. Trial choices have readable names and values.
- **Reports and native trade charts.** Inspect tear-sheet statistics, Optuna plots,
  trial settings, entry/exit markers and saved-price bar replay without exporting
  your work to another research website.
- **Save, pause and return.** Keep a named exact setup, reopen its evidence,
  reuse its rules with another capture, or resume compatible saved progress.
  Earlier studies preserve their original numerical versions.

Automatic v16 supports the admitted VectorBT NSE cash-equity profile, with up to
408 proposals and 512 account calculations. Broker-volume participation requires
qualified volume provenance; other brokers can use supported methods without
that assumption. Optional Nautilus fixed/custom calculations have their own
supported contract and Linux/WSL runtime.

[Branch notes](docs/research/releases/0.1.0-preview.30.md) ·
[Patch qualification and limits](docs/research/releases/0.1.0-preview.30.md#qualification) ·
[Future roadmap](docs/research/DELIVERY_PLAN.md).
Preview.14 combines completed fit-account/proposal saves and speeds up bounded
evidence serialisation, alongside the preceding cache and calculation corrections.
It includes the preview.10 storage-scan and earlier automatic-worker fixes.
The patch preserves existing calculation versions and search budgets. Broader
methods and assets are outside this essential one-click batch.

## What do I need to download?

**Download the complete OpenAlgo Research ZIP above, extract it, then run
`Setup.cmd`. Do not download `Setup.cmd` on its own.** It needs the application
files beside it. You do not need to clone or install OpenAlgo, VectorBT or Optuna
from their separate repositories.

| Component | Its job | What you install |
| --- | --- | --- |
