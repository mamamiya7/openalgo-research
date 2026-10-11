# Automatic trailing selection — 11 October 2026

## Plan and dependency order

The next calculation increment is conditional trailing activation, before wider
asset and clock search. The native VectorBT adapter already implements trailing
protection; the gap was automatic admission of its enabled and disabled states.
Reuse that evaluator, the existing causal stock/market context and validation
protocol. Add a new recipe rather than changing the meaning of saved studies.

Completion requires finite planning, actual whole-account calculations, durable
pause/resume, candidate inspection and exact replay, native charts from recorded
proposals, and a simple setup journey. Broader timing, volume, account-risk search,
additional assets and background diagnostic execution retain separate gates.

## Implemented calculation contract

`automatic-hypotheses-v6` retains v5's 92 stock/market choices across 32 families,
128 proposals and maximum 320 calculation reservations. It adds a frozen
`automatic-trailing-states-v1` descriptor for each strategy. Each categorical
proposal is either zero (off) or a positive trailing distance (on); disabled
proposals do not have an irrelevant second distance parameter.

Positive distances use the existing deterministic, at-most-seven-point range
around the saved positive distance, including a dormant distance, or the original
stop distance when none is saved. This is a finite research preset, not an
estimate of an universally optimal trailing distance. Every condition family
receives off, low-enabled and high-enabled settings: 96 screening proposals,
followed by 32 refinements. Optuna proposes the per-strategy states jointly with targets, stops and active
holding limits. Screening is bounded and does not cover every condition/exit
combination. The unchanged original portfolio is evaluated separately and keeps
its exact dormant distance, timing, cash, allocations, sizing, exposure and costs.

The existing VectorBT whole-account evaluator owns fills, shared cash and P&L.
Its completed-bar trailing high-water mark, protection on the entry bar,
stop-first OHLC ambiguity and gap handling are unchanged. Minute and daily
execution retain their own candle semantics. The new state consumes no extra
history and creates no downloader, database, numerical engine or worker.

Eligible candidates must still pass both development checks, higher costs and
bounded neighboring settings. V6 neighbors include activation and adjacent
enabled distances alongside existing exit challenges. Selection is frozen before
the final later period; the final period never reranks candidates. Short history
remains exploratory. Retaining original settings is a valid conclusion.

## Connected trader journey

Chartink/CSV → setup → **Optimize automatically** → native prices → joint search
and checks → findings → candidate report / saved trades / study plots →
Keep/Choose → saved setup → exact replay or another CSV.

New unconfigured VectorBT optimisation selects v6; Advanced research options keep
older recipes available. Existing saved scopes and custom ranges retain their
identity. The preflight disclosure states that trailing activation is searched,
and explains distance as conditional on activation. It does not add another
required setting or a new page. Findings show the exact selected TP, SL, holding
and trailing state in one compact line. Saved rules and ordinary report settings retain
the selected enabled state and distance. Nautilus keeps its qualified exit recipe
and rejects this VectorBT-only scope.

Recorded Optuna plots reconstruct v6's numeric categories only from the frozen
trailing descriptor. Booleans, changed domains, missing contracts and unregistered
numeric categories are rejected. Analysis runs no sampler or simulator. The
existing eight-category fANOVA bound remains; broad 92-choice hypothesis studies
retain their other native plots and an honest unavailable-importance explanation.

## Acceptance and resource scope

Focused qualification covers conditional domains, all-family activation screening,
unchanged reference identity, actual native VectorBT calls, on/off selection,
chronological checks, checkpoint replay, selected-state reuse, joint context
routing, paused study inspection and recorded native plots. UI qualification
checks the new default, retained saved versions, VectorBT-only admission, progress
and report labels. Exact counts and activation evidence are recorded in STATUS
and the ignored local acceptance receipt after verification.

The FD audit is scoped to this increment. The new helper and planning code are
pure. The evaluator still owns its existing bounded run-local context and gate
caches, four windows and 32MiB checkpoint bound. No files, database sessions,
sockets, threads, global caches or lifecycle registrations are added. This is a
static resource review, not a production broker soak test. The normal installation
must preserve configuration and saved run/artifact identities before restart.

Public preview.7 remains a separate published package. This local increment does
not certify a new release, all brokers or additional asset classes.
