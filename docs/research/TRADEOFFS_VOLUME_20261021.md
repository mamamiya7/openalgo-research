# Saved alternatives and volume admission — 21 October 2026

## Delivered scope

This continues the [v12 turnover and recommendation work](TURNOVER_RECOMMENDATION_20261021.md).
It adds a usable, read-only part of package 5 and the evidence boundary needed
before package 4 can make volume-based capacity claims. It does not change the
automatic recipe, search domain, calculation budget, saved recommendation or
numerical identities.

Completed automatic v8–v12 studies offer **Compare alternatives**. The application
reads the owned, exact saved result on demand and compares the recorded final
development-fit accounts. It never runs another engine, downloads prices, expands
account histories or uses final-period outcomes. Alternatives open the existing
settings dialog; report preparation remains a separate explicit action.

## Comparable development accounts

The pure `research-development-tradeoffs-v1` analysis verifies settings identities,
original input evidence, development dates, starting capital, costs, execution
versions and fixed user ceilings. Candidate allocations and order sizes cannot
exceed the frozen declarations; older recipes retain their fixed-setting rules.
Entry-condition and timing differences need their recorded contracts. Conflicting
duplicate identities and unsupported assumptions do not become comparable rows.

Display admission requires positive recorded return, no more than 25% drawdown,
five closed trades and five distinct closed-entry dates. These are explicit
exploratory display criteria, not a new recommendation policy or confidence test.
The frontier maximises return while minimising drawdown and actual closed-trade
count. Complete frontier counts are retained; at most four deterministic roles
are shown: balanced, higher return, lower drawdown and fewer trades. Roles may
share one account. An unchanged reference that matches or dominates a candidate
suppresses that alternative, including the case where no alternative improves
the reference. Missing reference metrics are not invented.

Every alternative remains exploratory. It does not inherit the selected account's
later-window, higher-cost, neighbour or uncertainty checks. Fewer closed trades
does not prove lower turnover, liquidity requirements or stronger evidence.
Only evaluated settings are compared; this is not a universal optimum.

The authenticated endpoint is
`GET /scanner-research/api/portfolio/jobs/<id>/tradeoffs?expected_result=<sha256>`.
It checks ownership before reading one result artifact, caps that read at 64 MiB,
and rechecks the result pointer, status and ownership before responding. Changed
results return 409; owner changes return 404. Responses are private and uncached.
The analysis accepts at most 1,000 rows and 16 MiB of relevant metadata. Closing
the view cancels its request; account, run or result changes reset the disclosure.

## Why capacity remains unavailable

The current frozen execution snapshot omits volume. The market-series contract
also records volume as unavailable. Native normalisation can replace an absent
volume field with zero; Historify rows alone do not retain enough provider,
supplied-field, unit and corporate-action lineage to distinguish those cases.
Positive values do not establish share units or a compatible quantity basis.

The new pure `research-volume-admission-v1` boundary accepts only an explicit,
hash-bound daily NSE cash-equity OHLCV receipt and reviewed producer attestation.
It requires share units, compatible price/quantity adjustment bases, instrument,
adapter and calendar bindings, field-presence records and observation availability.
The window contains exactly the prior 20 expected completed sessions, excluding
the entry session. Gaps, late observations, ambiguous zeroes and mismatched
identities return unavailable. An explicitly observed zero is distinct from an
unknown normalised zero.

Sources are bounded to 3,000 rows and 2 MiB; quantities to the exactly representable
integer range. Results retain a compact sealed causal window and source identity.
Hashes establish integrity, not independent provider authenticity. This is a
trusted internal producer contract, not a user-declaration endpoint.

**No existing acquisition path produces this new attestation yet. No capacity cap
or volume-based sizing is enabled by this increment.** Old evidence is never
silently upgraded, existing OHLC acquisition is unchanged, and no generic OpenAlgo
database migration is required. Next: retain qualified volume provenance through
native acquisition and freezing, then implement causal participation limits and
test rounding, missingness, corporate-action basis and recovery before exposure
in automatic optimisation. Broader recommendation calibration remains planned.

## Qualification and installation

319 distinct backend checks pass across saved-candidate/comparison contracts,
evaluation identities, distribution checks, native feature boundaries, volume
admission, saved alternatives and the v12 programme/recovery. This includes 78
pure trade-off checks, 63 volume-admission checks and 13 owned-read service checks.
The connected v12 test uses real Optuna and native causal gates with synthetic
account economics; it verifies selection/recovery rather than claiming market
edge. The stricter cohort checks are applied to its actual result, preserve the
result and add no engine calls.

33 frontend checks pass, including accessible disclosure, stale pointers, request
cancellation, account changes, missing reference metrics, reference dominance and
the connected exact-settings dialog with focus return. TypeScript, Biome, Ruff,
source compatibility and the production build pass. Full fresh Windows installation
and a new real-broker study were not rerun for this read-only increment. Development
uses isolated data and no broker credentials or orders. Public release ZIPs are
separate from source publication and local activation.

Resource review: both pure modules own no files, connections, workers or caches.
The read service uses existing bounded artifact readers and context-managed SQL
sessions. Repeated successful and rejected reads check handle counts, session
closure and retained memory. The UI releases pending reads when closed and keeps
its result cache only while mounted.

Across 100 successful and 100 rejected service reads, Windows handles changed
329 → 313, retained traced memory was 8,488 bytes, and observed SQL connections
and closures balanced. Qualification receipts are retained under the ignored
local `capacity-core-20261021` checkpoint directory.

Graph orientation used the historical Graphify map; current source and the
delivery plan controlled implementation. Windows application control prevented
a fresh Graphify CLI rebuild; this is not represented as an updated graph.

Activated locally from `6f4b9cd43`. The existing account, served production UI,
authenticated alternatives route, installed compatibility and fresh worker
heartbeat pass. All 63 saved run identities, configuration and Historify bytes
are unchanged. Source/UI rollback files are retained locally; no normal research
or trading job was started. Source is pushed to `codex/native-research-refinement`;
public release ZIPs remain unchanged.
