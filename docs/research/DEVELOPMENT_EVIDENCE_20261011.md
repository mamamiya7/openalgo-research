# Frozen research mandate and development account evidence

Implemented in development source on 11 October 2026. This is the first feature
foundation in the [delivery sequence](DELIVERY_PLAN.md#feature-development-order-after-the-human-audit).
It is additive to existing VectorBT automatic hypothesis recipes v2–v7; their
proposals, calculation limits, settings, accounting and selection rules remain
unchanged. This increment is not yet installed in the normal app or published.

## What the user receives

The automatic workflow remains import → confirm trading assumptions → optimise →
inspect findings → save a tested setup. Existing collapsed **Research details**
shows a small saved-history count. Partial or unavailable histories are labelled
accordingly. This adds no setup choice, panel, alert or claimed confidence score.

Before the first calculation, Research freezes a mandate containing source and
price identities, initial settings, capital, costs, engine and interval, admitted
methods, search objective, bounds, budgets and development/final period roles.
Resume reconstructs it from the same frozen inputs. The mandate does not certify
that a period has never been viewed; the existing account period-use service still
owns reservations. Legacy calculation checkpoints resume under their original
contract, without fabricated mandates or retrospective downloads/calculations.

For each distinct scored development evaluation, Research retains the full actual
account curve, original daily/minute observation keys, cash, open-position counts,
drawdown and account returns. Candidate settings, causal condition receipt,
evaluation basis and cost scenario bind those histories to their scored rows.
Repeated evaluation identities share immutable content and must reproduce it.
The selected account's ledger replay is checked against its retained history.
Reserved final-period accounts remain separate in the existing result contract.

## Persistence and bounds

- `research/research_mandate.py` builds/verifies the bounded pre-outcome receipt.
- `research/development_evidence.py` plans, constructs and validates account shards.
- `services/research_development_evidence.py` owns a job-local small index and
  publishes shards through native immutable artifact storage.
- The calculation checkpoint keeps references, never all candidate curves. Both
  worker and wrapper preparation checkpoints retain the index and mandate.
- Native nested `inputs_artifact` references keep source and shard files in the
  existing backup/restore/pruning closure. Publication finishes and releases its
  SQL/file ownership before checkpoint persistence.

Admission caps are 320 distinct development evaluations, 100,000 marks per
evaluation, 4 million marks in total, 24 MiB per shard and 128 MiB logical shard
content in total. Conservative retention estimates precede calculations. These
bounds supplement the existing global disk quota and physical chunk/file limits;
128 MiB is not a promise for compressed physical disk usage. Resume admits the
small index and its totals before opening any shard, then verifies one bounded
file at a time. Small raw/gzip artifacts do not allocate the entire caller limit.

If retention cannot qualify or fit, the original bounded calculation can continue
with explicitly unavailable/partial development evidence. A quota/write failure
still stops durably rather than publishing a nonexistent history. Dates are never
resampled or filled. A zero preceding equity leaves the next simple return
unavailable; it does not invent a zero return or erase the loss.

**Complete** means all distinct development evaluations actually scored by this
programme were retained. It does not mean an exhaustive search, a rectangular
candidate-by-fold matrix or statistical proof of an optimum. Different candidate
cohorts and cost scenarios retain their identities; later diagnostics must qualify
comparability instead of pooling them blindly. No new PBO, bootstrap, probability
or rolling-validation claim is made by this increment. Nautilus and older
non-hypothesis recipes retain their previous evidence paths.

## Verification and next dependency

Focused checks cover exact daily/minute marks and returns, source/settings/cost
identity, corrupt/rehashed receipts, bounded raw/gzip/chunk reads, storage closure,
publication failure, resume before and during calculation, and legacy bypass.
Seeded real Optuna replay preserves proposals, engine-call order and the exact
numerical result; a native VectorBT account verifies full curve retention.
Current v7 service recovery is included alongside the historical v2 fixture.
Repeated successful/failed reads and resume/retention paths are measured for
stable handles and retained memory under the OpenAlgo resource-audit guidance.

Recorded acceptance: 153 backend checks passed, followed by 30 affected
bounded-read/chunk/resource checks after the small-read allocation refinement;
116 scoped frontend checks, TypeScript build and lint passed. The paired
120-iteration resume/success/failure audit retained 340 → 340 handles, 120 traced
bytes and approximately 235 KiB peak traced allocation on the recorded Windows
runtime. The separate 150-iteration read/rejection audit retained 324 → 324 handles
and zero traced bytes. These receipts measure this storage change, not overall
application memory or broker performance.

The next feature dependency is rolling chronological validation: compile fold
roles and training-only fitting, qualify holding-overlap purges, preserve fresh
account semantics for each fold and reserve final verification capacity before
expanding method search. This source foundation supplies its durable inputs;
rolling validation itself remains planned.

**20 October follow-up:** the [v8 expanding-refit slice](WALK_FORWARD_20261020.md)
now uses this foundation in development source. The acceptance above remains the
record for this earlier storage increment; it is not a new installation or release.
