# Expanding research validation — 20 October 2026

Development source only. This extends the [saved-evidence foundation](DEVELOPMENT_EVIDENCE_20261011.md)
under a new `automatic-hypotheses-v8` recipe. It does not reinterpret v2–v7 reports,
replace native VectorBT accounts or publish a new distribution.

## What happens after Optimize

The app freezes settings, costs, allowed methods, chronology and budgets before
calculating an account. On the first 80% of recorded signal sessions, it performs
three expanding-prefix fits, each followed by its own later, non-overlapping check.
Each fit has a fresh seeded Optuna sampler and 48 proposals. Only that fit's earlier
account outcomes are told to its sampler. A completed earlier check may become
past training history for the next fit; it cannot revise an earlier choice.

The procedure then refits on the whole development prefix, with another 48
proposals. Its candidate can advance only if all three earlier checks and their
higher-cost comparisons passed. Otherwise the unchanged original settings remain
the choice. Selection is saved before evaluating the reserved final 20%; final
outcomes can reject support but cannot rerank settings or restart a search.

The native findings panel gives a short conclusion. Existing Research details
contains the three actual fit/check date ranges, selected settings and ordinary
and higher-cost account comparisons. The trial table and Optuna diagnostics show
the final development fit's 48 proposals; mixing scores from different windows
would misrepresent a single study. Progress counts all 192 proposals. Activity
labels the fit and its local trial number, retaining the overall proposal number
as a reference; scores belong to that fit's training dates.

## Financial and data contract

- Every account starts from the same fresh cash with the original risk, cost and
  fill assumptions. Fold returns remain separate. There is no stitched carried
  portfolio, compounded reset-account return or aggregate confidence score.
- Holding deadlines and admitted clock alternatives must fit wholly inside their
  own period. Daily admission counts distinct dates after conservative maximum-hold
  purging; minute slicing still checks the actual recorded clocks. Short or sparse
  history stops admission; a horizon is never shortened to force a result.
  Existing native timeline/matrix capacity limits also remain in force; this
  increment does not qualify arbitrarily large intraday portfolios.
- Every fit compares against unchanged settings on the same account window.
  Higher-cost checks use `max(2 * cost_bps, cost_bps + 5)` and unchanged slippage.
  Selection requires positive after-cost performance, sufficient completed trades
  across dates, the existing drawdown limit and an improvement over the reference.
- All proposals reuse one frozen Historify stock/index union. No proposal downloads
  its own data. Registered fixed causal formulas and domains are reused; this
  increment does not add learned models, transformations or new indicator methods.
- The 92 registered stock/market choices span 32 families. Each fit screens one
  representative per family, admits up to eight clock probes and reserves at least
  eight adaptive proposals. Registration is not a claim to exhaust every choice
  or the Cartesian product. V8 does not yet perform per-fit neighbour checks.

## Recovery, bounds and ownership

There are 192 proposals across four independent fits, with at most 320 reserved
native calculation attempts. Two calls are reserved for final comparison; up to
64 lost attempt reservations can be recovered. Duplicate settings inside a fit
reuse their exact account evidence. Full histories for all evaluated development
fits, ordinary checks and cost scenarios use the foundation's immutable shards;
final evidence stays separate. Resume verifies the mandate, frozen inputs, seeded
proposal replay, completed fold receipts, selection and retained histories.

The new workflow owns a detached, validated stock/index projection for its job
lifetime. It validates the root and projected context once, reuses the unchanged
numeric gate implementations, and releases ownership on success, cancellation or
failure. Public legacy gates keep their full input validation. Window and gate
caches remain bounded to two and eight entries respectively. No process-wide
history or feature cache is introduced.

## Qualification and remaining scope

Qualification covers pure chronology/admission, real Optuna proposal replay,
later/final-outcome isolation, pause/resume, tamper rejection, native VectorBT
period accounts, seven-period development persistence, progress-log ordinals and
bounded resource cleanup. The prepared gate seam is checked byte-for-byte against
all 92 registered public gates and legacy context regressions.

Recorded acceptance: 587 distinct current targeted backend cases and 217 frontend
cases passed, together with TypeScript, scoped lint and a production UI build.
The count excludes one replaced early protocol test and does not add repeated
suite runs. Resume preserves proposals and results without recalculating completed
accounts. Altered completed fold receipts, frozen selections and saved report
summaries are rejected. Resource tests cover 100 native gate reuses and 100
success/cancellation/failure ownership cycles; an already cancelled job does not
copy its history.

Five measured real gates took 0.158s using a prepared context versus 0.741s through
the public validating path, plus 0.037s preparation. After 100 reuses, process
handles stayed 607→607 and retained traced allocations were approximately 16KB.
These are isolated gate measurements, not a live broker or whole-run speed claim.
Checkpoint hashing, account calculations and artifact persistence remain material.

A complete isolated run with real VectorBT 0.28.5 accounts, Optuna 5.0.0 and native
gates completed 192 proposals and 204 account calculations in 107.06s, or 118.15s
including imports, fixture preparation and native warmup. It retained 19,055
actual equity marks and 609 hashed checkpoints. The one-stock synthetic-price
fixture correctly returned no supported improvement and retained the original
settings. This check deferred optional plots and did not include database artifact
persistence or broker acquisition; separate service checks cover development
history retention and recovery.

This implements the expanding-refit slice of delivery package 2. Learned
transform fitting, continuous carry, statistical uncertainty, richer robust
selection, new method waves and broader asset admission remain later work.
There is no claim of a universal optimum or that three passing windows establish
future profitability. Normal installation and public release are separate actions.
