# Development update — 30 September 2026

Implemented in development source and installed in the normal local application
on 30 September. **Not publicly released or published.**
The published Research version remains `0.1.0-preview.4`; its distribution
manifest now identifies the development host pin. No release was created.

## Host integration

The reviewed host range is OpenAlgo 2.0.2.5,
`d858c2384cbd6a9bdb09d0fb42c1a08b46c48c29`, to 2.0.2.6 plus subsequent fixes,
`f5165699dfa3925d750c9cf2ef73d586b62611ba` (258 upstream commits).
Integration preserves research routes, owner-bound MCP tools, native supervisor,
historical evidence/cancellation controls, dependencies and source additions.
Fyers streaming/auth/depth and master-contract fixes, Historify lifecycle and
sequence migration, OpenScript 0.5.0 and shared chart engine 2.5.1 are included.
The optional gthread server is not enabled.

Original source hashes/backups and merge decisions are in the ignored checkpoint
`.agent-native/update-20260930/host-integration/`. The integration removed only
the obsolete `services/telegram_bot_service_fixed.py` and `services/telegram_bot_service_v2.py`
host source files; their original contents remain recoverable in that checkpoint.
Runtime folders, credentials, broker/live databases and generated normal-app
frontend assets were excluded. Existing dirty research changes were preserved.

## Research increment

See [the exact automatic contract](AUTOMATED_RESEARCH_PLAN.md#bounded-market-entry-extension--development-30-september-2026).
The new opt-in VectorBT recipe screens four predefined causal Nifty entry presets
after exits-only search, performs whole-cash account simulations, and records a
final result without reranking. It retains a no-filter baseline and reports
inconclusive evidence rather than manufacturing a winner.

Rules are versioned and part of configuration identity. Exact replay uses frozen
market inputs, without fresh downloads. Fixed-condition custom optimization,
shortlist, Keep, chosen setups, restore and new-CSV reuse preserve their rules.
Later-test evidence cannot silently become selection evidence. Additive metadata
records account-wide used dates and original input artifact/job identities;
cancelled/orphaned claims survive pruning and backup/restore. New launches cannot
relabel identical data as a resume. External prior viewing is not knowable.

One intentional numerical-input correction converts admitted whole-number OHLC
arrays to floating point in regime features: these inputs previously raised a
NumPy casting error. Existing floating-point formulas and saved artifacts are
not rewritten. Old exits-only recipes and unfiltered configuration hashes remain
unchanged.

## Verification and qualifications

The development tests used isolated synthetic data, database roots and subprocess
state. A subsequent authorized normal-app restart activated the matching source/UI;
see STATUS.md. Aggregate research coverage is **1,634 passing
cases and 49 skipped** across the broad run and corrected targeted reruns;
the final feature-specific batch passes 31/31. Full frontend coverage passes
**3,678 cases**, with TypeScript and an isolated production build. Full counts,
initial fixture failures/retest details and resource review are recorded in
`.agent-native/update-20260930/VERIFICATION.md`.

Acceptance covers seeded searches, causal prefix invariance, pause/recovery,
failed-final non-reranking, actual HTTP upload/worker/export/exact replay,
same-data fresh-launch refusal, real fixed-condition Optuna alternatives through
Keep/chosen setup, tamper rejection, populated backup/restore/pruning and date
claim persistence. Full frontend tests, TypeScript and an isolated production
asset build are included. Host tests exercise Fyers, Historify, OpenScript, MCP,
bounded caches, concurrency and cleanup.

The required resource audit checks transaction/context closure, acquisition
ownership, run-local bounded caches and lifecycle error paths. Repeated fixture
checks measure handles/RSS and retain no growing connections. This is not a
long-running live broker audit. Eventlet-specific cases need Linux; Windows-only
synthetic checks do not certify production eventlet. A normal-app browser smoke
test and controlled restart subsequently passed locally. The user's completed
daily backtest also records Historify reuse and missing-price acquisition through
Fyers; this does not qualify every broker or the automatic-condition journey.
Broader live broker qualification
and refreshed source-package acceptance/release/publication remain separate steps.
The older packager's untracked-host inventory needs requalification before a
distribution is made from this dirty upstream integration. No orders, strategies,
trading-mode changes or credentials were used.

Stock-specific indicators, arbitrary stacked filters and rolling walk-forward
adaptation are still future work. Research improvements are hypotheses, not
proof of future profitability.
