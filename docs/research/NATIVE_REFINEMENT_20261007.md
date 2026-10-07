# Native research refinement implementation

This increment sharpens saved inspection and adds a bounded stock and market
search on the current native OpenAlgo host. The development package is
`0.1.0-preview.8` on OpenAlgo main `ee26e4e24cae`; the public download remains
preview.7. Charts 2.6.0 and OpenScript 0.8.1 are separate host dependencies.

## User journey

Upload or import signals, select VectorBT, and choose **Stock and market** in
automatic research. The existing Smart research scope remains the default.
OpenAlgo checks its native Historify archive and acquires missing broker history.
Optuna proposes complete settings and VectorBT recalculates the shared account.
Saved trials can be inspected, replayed, kept, chosen and reused through the same
Research library journey. More signals or a longer holding period can require
more native prices; changing only a display preference requires none.

On a saved trade chart, **Values** opens native candle and indicator readings.
Built-in indicator parameters and styles survive switching into and out of
replay. Rewinding recalculates readings from the completed prefix. Chart drawings,
computed values and future exit annotations are not copied into replay.

## Calculation contract

| Part | Behavior |
| --- | --- |
| Registered hypotheses | 23 stock hypotheses crossed with four market choices: unrestricted, rising Nifty trend, normal Nifty volatility, or high Nifty volatility |
| Search | 92 registered choices in 32 families; 96 screening proposals and 32 refinement proposals; at most 320 account simulations |
| Stock inputs | 100 recorded prior daily sessions plus the required execution window; the existing indicator and relative-strength calculations remain versioned |
| Market inputs | One shared Nifty history with 160 prior recorded sessions, enough for the established causal classifier |
| Entry decision | Stock and market gates judge the original signals independently; both must allow entry; missing required observations remain unknown |
| Execution | Daily or minute execution remains determined by signal timing and trade settings; daily feature history does not replace intraday execution history |
| Selection | Existing chronological search, development checks, neighboring settings and reserved later evaluation remain; infeasible splits stay exploratory |
| Evidence | New joint context and rule versions are retained, including calculation/runtime identities; old v2/v3/v4 recipes keep their old behavior |
| Progress | Display history is sampled to at most 100 real score points; all proposals remain in the durable study |

This is a bounded search, not an exhaustive evaluation of every possible
indicator, Boolean expression or exit combination. Capital, sizing, allocation
and exposure remain fixed for the original comparison. A supported candidate
does not imply future profitability; the unchanged original can remain selected.
Observed volume, original scanner reconstruction, arbitrary regime stacks and
unqualified indicators are not silently admitted.

## Qualification and delivery

Focused tests cover independent gate agreement, unknown inputs, future-price
invariance, fixed-rule identity, real Optuna pause/resume, real VectorBT execution,
native context recovery and broker-free replay. The complete native job journey
is separately checked through pause, partial inspection, resume, candidate report,
exact replay, saved decision and chosen setup reuse. Native chart tests exercise
the actual 2.6 replay controller and Data window, including repeated cleanup.

The public preview.7 installation instructions and bounded Historify read code
are reconciled with the newer host source. Packaging requires the new joint
service, shared chart Values helper and OpenScript host modules. Runtime databases,
credentials, downloaded prices and research artifacts stay outside Git and the ZIP.
The package version does not change the upstream OpenAlgo version label.

The source reconciliation also retains first-time broker setup before OAuth,
Chartink extension connection instructions, worker readiness and the published
bounded lease-heartbeat writes. Public extension icons and historical README
screenshots remain included. Startup, broker configuration and atomic-write
regressions are checked independently of calculation results.

Broader native chart navigation and whole-period curves, retained candidate
return matrices and advanced diagnostics, and a qualified read-only OpenScript
study export remain in the [delivery plan](DELIVERY_PLAN.md). This increment does
not close those phases or constitute a public release.

## Recorded acceptance

The complete native v5 job journey passed with all 128 proposals, real VectorBT
calculations and native Optuna plots, followed by candidate replay, decision,
chosen setup and reuse. The independent gate suite has 100 checks including
bounded progress; role-specific context has 51 checks. Saved chart changes have
216 distinct focused frontend checks across 14 suites, including actual native
replay and Data window behavior. TypeScript, scoped lint and production build
pass. Browser inspection confirms EMA persists into replay and its reading changes
on backwards stepping. This is controlled test data; it does not certify every
broker or predict real strategy performance.

## Local installation acceptance — October7

The normal application is activated from committed source `43e9f365b135` with
the existing account, configuration and all58 saved-run identities preserved.
The installed source/dependency compatibility check passed160 checks. No runtime
database or broker credential was copied into the development source or package.

The exact preview.8 candidate ZIP, built from that committed source, passed a
fresh Windows installation using its own downloaded Python3.12 runtime, new
account and controlled Historify candles. Acceptance includes broker profile
access before OAuth, daily archive reuse, real VectorBT calculation, three native
Optuna trials, exact selected replay, reinstall/restart with identical saved
exports, and release of owned ports and the instance lock. The ZIP SHA256 is
`b9f4f51067ebea1bedfda83dd666d587d17eedaace42c2bccffb9168c0963b33`.
The package's source compatibility check passed156 checks. Its manifest contains
no uncommitted included source changes; omitted changes are generated host assets.

This is local Windows acceptance, not a replacement for the public full runtime
matrix or a fresh Linux acceptance of preview.8. Public preview.7 remains the
published release. Live broker certification stays limited to its recorded cases.

The next implementation order remains shared whole-period account curves and
saved inspection, retained candidate return series/comparison, then wider
diagnostics and a qualified read-only OpenScript bridge. Additional exit branches,
engines and asset classes require their own numerical and recovery acceptance.
