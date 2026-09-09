# OpenAlgo Research release readiness

Updated 9 September 2026 for `0.1.0-preview.4`.

The deliverable is **one installable OpenAlgo Research source distribution**:
the native Backtest & Optimize screen, research coordination and worker,
VectorBT/Optuna adapters, and an optional separate Nautilus runtime. Users install
it with their own OpenAlgo account and broker connection. They receive an
integrated application; a separate research website or broker account is not
required. [DISTRIBUTION.md](DISTRIBUTION.md) owns installation and updates.

The application is usable locally and prepared for the public
[OpenAlgo Research preview](https://github.com/mamamiya7/openalgo-research/releases).
It is installed and run by its owner; distribution does not require a hosted
service. This checklist supersedes the earlier EOD-only and
official-file-first launch proposal, which did not meet the product requirement.

## Consumer journey

Open **Tools → Backtest & Optimize**, upload or reuse one to eight signal CSVs,
set strategy rules and allocations, choose Backtest or Optimize, then review the
combined account and strategy contributions. VectorBT and Optuna TPE are the
defaults. Advanced engines and settings stay in disclosures or drawers.

Daily and timed CSV examples are available behind **CSV format & examples**.
They contain illustrative observations, with no price or performance data.
Date-only signals with daily rules use daily candles. Timed signals or explicit
intraday rules use one-minute candles. A mixed portfolio uses the finest required
interval, with the whole optimizer range planned before the first trial.

Historify is the shared mutable price database. The connected broker's ordinary
OpenAlgo history service fills only missing required coverage. Research freezes
the exact stored prices for calculation and replay. Selecting sources, opening a
saved result and exporting evidence do not download prices.

Show the outcome, chart, contributions and useful next actions first. Healthy
services stay quiet. Data exclusions and actionable failures remain visible;
receipt identifiers, internal paths and full diagnostic detail belong in saved
evidence. Account review remains available without a current broker token.

## Release checks

| Check | Current evidence | Remaining before a broad public release |
| --- | --- | --- |
| Native data path | Real Fyers missing-minute acquisition, daily archive reuse and exact stored-price comparisons passed. Daily/intraday resolution and fixed optimizer coverage have automated checks. | Publish a precise tested-broker matrix. Exercise additional real adapters before claiming their support; controlled responses do not certify a live broker. |
| Calculation | Full-input VectorBT and Optuna, later-period separation, exact replay and optional real Nautilus calculations passed. | Keep declared settings and execution assumptions explicit; run the supported compatibility matrix for each update. |
| Calendar | Recognized NSE seed errors repaired; required-window admission covers 2025–2026. Migration preserves custom/other-exchange records and saved evidence. | Extend the reviewed baseline before later-year use. Verify newly announced special hours and model security-specific closing-auction phases before claiming that execution scope. |
| Install and update | Fresh Linux install/reinstall and fresh Windows directory/environment passed. Windows app/worker Ctrl+C and checkpoint recovery passed. Targeted calendar upgrade is repeat-safe. | Run the final artifact in the supported OS matrix and exercise an older-version upgrade, backup restore and rollback. A fresh directory is not a clean OS image. |
| Artifact and privacy | Source inventory excludes credentials, databases, private inputs, local receipts and history files; locks and hashes identify the candidate. | Review the exact final archive and public instructions. Retain upstream licence and attribution files. Distribute example signals, not an owner's price database or account configuration. |
| User acceptance | Desktop/mobile, keyboard, saved-source, examples, settings and report journeys have scoped checks. | Have intended OpenAlgo users complete upload → run → inspect → reopen without coaching; resolve blocked tasks or misunderstood outcomes. |
| Capacity and recovery | Bounded worker, admission, storage, resumable acquisition, checkpointing and exact saved evidence are implemented and tested. | Measure realistic CSV/search workloads on advertised hardware. Validate the documented backup/restore procedure against the final release. |
| Docker and support | Docker configuration and a locked optional engine runtime are included. Install/update/compatibility instructions and a [public issue route](https://github.com/mamamiya7/openalgo-research/issues) are provided. | Execute Docker build/start; it was unavailable locally. |

Detailed results, non-additive test checkpoints and evidence limits are in
[VERIFICATION.md](VERIFICATION.md). These checks do not require a profitable
backtest result. Research never places an order or activates a trading strategy.

## Supported preview scope

Long NSE cash-equity CSV signal portfolios, shared cash, whole shares, daily or
minute bars, supported holding/exit rules, costs, bounded optimization and a
separate later-period check. The native calendar baseline admits only the
reviewed years and confirmed required special-session hours. An unsupported
calendar window fails before a broker download.

The adapters record OHLC execution assumptions. They do not provide observed-tick
replay or security-specific auction modelling. NSE's closing-auction rollout is
confirmed by [circular CMTR75479](https://nsearchives.nseindia.com/content/circulars/CMTR75479.pdf);
generic session hours alone do not establish auction eligibility or fills.
Shorts, derivatives, leverage, shared margin, corporate-action cashflows,
arbitrary imported engine programs and automatic live handoff are outside this
preview. Nautilus additionally rejects unsupported trailing stops and configured
slippage. See [CONNECTORS.md](CONNECTORS.md) and [EXECUTION.md](EXECUTION.md).

An installation serves its owner using ordinary OpenAlgo authentication and
storage. A service for unrelated hosted customer accounts would need a separate
design and acceptance scope.

## Candidate-to-release sequence

1. Build the exact reviewed source archive and verify its file hashes and
   exclusions. Keep earlier candidates available for rollback.
2. Install that artifact in disposable supported environments. Verify setup,
   account login, the compiled screen, research worker and locked engines.
3. Run daily and timed CSV journeys. Prove existing Historify reuse, actual
   missing-price acquisition, correct persistence, exact replay and recovery.
4. Complete upgrade/restore/rollback, representative capacity and unaided user
   checks; record the supported platform, broker and execution matrix.
5. Publish the reviewed repository and release artifact when authorized. Public
   hosting and trading activation are separate actions.

[STATUS.md](STATUS.md) records current scope. [RUNTIME.md](RUNTIME.md) covers
process operation, [STORAGE.md](STORAGE.md) backup scope, and
[SYSTEM_MAP.md](SYSTEM_MAP.md) component ownership.
