# Runtime profile before portfolio-wide allocation

Measured on 21 October 2026 against the existing v15 native calculation path,
before extending its allocation search. This is a scoped engineering profile,
not a prediction of every user's run duration.

## Scope and method

An isolated Python 3.12 process used the genuine VectorBT connector and existing
native synthetic-market fixture. It did not connect to a broker, read live
account databases, start the application or place orders. The representative
search-window account has one strategy, two symbols, 128 daily sessions and 256
signals; 250 trades close. This deliberately small, populated account exposes
controller overhead separately from larger-universe simulation cost.

The first native call is measured separately from four uninstrumented warm
calls. One additional warm call and a bounded controller prefix use `cProfile`;
instrumented cumulative times include profiler overhead and overlapping calls.
The prefix commits eight real Optuna proposals, calculates its baseline and
eight candidate accounts, writes actual immutable development shards, and
serializes/writes checkpoints before a planned pause. It does not finish or
qualify a complete search. Its local checkpoint writer is not a benchmark of
the application's complete service/database persistence layer.

The ignored `.agent-native/portfolio-allocation-20261021/profile*` files retain
the executable measurement, raw timings, cumulative call profiles, checkpoint
and shards. The existing frozen native calibration receipt remains separate.

## Measurements

| Operation | Measured duration |
| --- | ---: |
| Build synthetic fixture and attach search-window evidence | 3.058 s |
| First native account call, including lazy import/initialization | 11.470 s |
| Warm native account, four unprofiled calls | 0.827–1.034 s; median 0.969 s |
| Warm native account under `cProfile` | 1.680 s |
| Copy complete native report, median of 20 | 25.47 ms |
| Fingerprint complete native report, median of 20 | 8.38 ms |
| Serialize complete native report, median of 20 | 7.40 ms |
| Audit already attached complete account, median of 20 | 75.93 ms |
| Dataset cache miss, including retention | 830.34 ms |
| Exact dataset cache hit, including defensive copy | 39.13 ms |
| Eight-proposal controller prefix under `cProfile` | 30.459 s |

The full search-window report occupies 807,918 bytes as compact JSON. A 64 MiB
cache therefore cannot retain 512 reports of this size; its byte limit would
bind first, before Python object overhead. A single repeated account benefits
from exact reuse, but that does not establish a whole-search speedup. The earlier
15-programme native qualification measured only 17 exact reuses among 4,459
account requests, so increasing the cache or assuming broad hits is unjustified.

## Where the measured time goes

For the instrumented full search-window account, native simulation uses 0.869 s
and report construction 0.793 s. Native analysis uses 0.699 s, including 0.588 s
in VectorBT statistics. Simulation and reporting both matter.

The smaller controller-prefix accounts expose additional overhead. Nine native
calls use 8.040 s cumulatively. Actual constraint audits use 6.820 s;
portfolio-feature attachment uses 5.384 s; stock/market gating uses 4.239 s;
execution-feature attachment uses 2.754 s. These are overlapping cumulative
measurements, not additive independent buckets.

Repeated canonical JSON integrity work is a material contributor:
`portfolio.fingerprint` runs 15,089 times and accounts for 10.540 s cumulatively;
`stock_features._json_size` runs 2,901 times and accounts for 7.469 s. Native
analysis consumes another 5.262 s across these accounts. Integrity checks must
remain, but immutable, already verified evidence should be projected/reused
within a bounded calculation scope rather than repeatedly traversed per entry.

The 26 measured checkpoint serialization/write callbacks together take less
than 0.05 s, and the nine shard build/serialization/write operations together
take less than 0.05 s. At this prefix, local checkpoint disk writes do not
explain the runtime. This does not rule out costs at maximum checkpoint size,
on a slower disk or in the full service persistence path.

## Budget for the next allocation slice

Keep the existing 408-proposal and 512-account-attempt ceilings. Introduce one
bounded allocation policy and a matched reference within that envelope; do not
multiply every search axis or add sector/factor/rebalancing searches. Reserve
capacity for the existing chronological, friction, sensitivity, seed and final
checks before admitting candidates.

Compute causal prior-return portfolio features once per frozen data scope and
reuse them across candidate accounts. Avoid repeated matrix construction or
whole-bundle integrity scans inside a fill callback. Missing or singular inputs
must produce explicit supported fallback/unavailability evidence; they must not
cause hidden data acquisition or an expensive alternate search.

Before any broader search, repeat a matched baseline/new-policy account profile
and genuine recovery/audit checks. Preserve old recipe identities, complete
account histories and financial semantics. If later optimization of report or
integrity work is attempted, prove numerical and saved-evidence equivalence;
this profile alone does not authorize dropping metrics or weakening validation.


## Matched allocation follow-up

After implementation, the same genuine account was measured under the previous
policy, the new policy with original allocation, and the new cluster allocation.
Three warm calls each had medians 0.7263 s, 0.7246 s and 0.7420 s respectively.
All three genuine constraint audits were supported; the old policy and new
original policy have exactly equal native orders, summary and equity curve.
Allocation caps were nonbinding in this account; separate binding native tests
cover actual changed sizing. Small timings under shared-machine load are not a
larger-universe throughput or whole-search promise.

A scoped pure-builder resource repetition covered 100 twenty-symbol calls after
five warmups and a separate 200-symbol bound smoke. Handles stayed at 204, RSS
rose only 57,344 bytes and plateaued, with no persistent cache or external handles.
This measures the new builder, not the maximum full-controller working set.


## Complete native regression cost

The frozen v16 canonical programmes on independent-noise and persistent-edge
markets completed in 592.42 s and 841.72 s respectively while running in two
bounded OS workers on this shared Windows machine. The former stops after its
272 proposals when earlier evidence vetoes a change; the latter uses all408
proposals and six fits. They include genuine calculations, audits, retained
histories and planned pause/resume. They are two engineering workloads, not a
consumer throughput SLA or independent confidence estimate.

This reinforces the next performance priority: reduce repeated evidence/report
work while proving identical financial decisions, required metrics, immutable
histories and recovery. Larger symbol/minute workloads still need their own
profile. Broader method/search expansion waits behind that measured work.
