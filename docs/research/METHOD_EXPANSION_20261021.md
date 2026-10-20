# Automatic method expansion — 21 October 2026

## Core capability

`automatic-hypotheses-v9` expands the native automatic research programme from
92 to 108 registered stock/market choices, across the existing 32 families.
It adds absolute momentum over 10 and 60 completed sessions, at zero and 2%
thresholds, with each of the four existing market presets. The previous 92
descriptors remain an exact prefix. Old v2–v8 studies keep their own recipes;
reopening a saved setup never silently upgrades it.

New unconfigured VectorBT setups select the expanded programme. The user still
imports signals, confirms trading assumptions and clicks Optimize. No new
parameter form, engine, data provider or independent downloader is introduced.
Nautilus retains its separately qualified programme; library capabilities do
not imply unsupported connector capabilities.

## Data and search

Momentum is `100 * (last_close / close_N_sessions_ago - 1)`. It requires N+1
consecutive recorded closes before the entry session, with recorded availability
no later than the actual decision. Equality within the existing 1e-12 relative
or absolute tolerance does not pass. Missing observations remain unknown and
exclude the affected entry. This is price momentum, not RSI, relative strength
or total return.

The existing 100-session stock warmup already covers the 61 closes required by
the longest added method. The shared 160-session market context is unchanged.
Historify/history prepares missing admitted windows once, then every proposal
uses the same immutable native price evidence. Daily inputs stay daily; minute
execution can use these prior daily features without claiming minute indicators.

Each of four independent fits retains 48 proposals:

- 32 unchanged family representatives at original clocks and midpoint exits.
- Four explicit original-clock probes, one for each new stock momentum condition.
- Up to four admitted clock probes; other registered clocks remain in refinement.
- At least eight seeded Optuna joint refinements over the complete 108-choice domain.

The total stays 192 proposals and at most 320 native calculations. Compared with
v8, four reserved clock probes per fit are reassigned to the new momentum methods.
The three later-window and higher-cost checks, fresh equal cash per account,
reserved final comparison and original-settings fallback are unchanged. Later or
final outcomes cannot rerank earlier fits. Untried/deferred conditions remain
visible under Methods considered; considered does not mean exhaustively tested.

## Saved evidence and recovery

The new feature identity is `research-expanded-stock-market-features-v1` and its
chosen entry descriptor is `research-joint-entry-conditions-v2`. Method registry,
frozen mandate, trial/report reconstruction, fixed-condition replay, saved setup
reuse, activity ordinals and optional Optuna diagnostics recognise these versions.
The Optuna plots describe the final 48-proposal fit, not pooled independent fits.

Full integration testing exposed a cache-dependent component identity during
chosen-account reconstruction. V9 now hashes causal component membership,
features and entry-source identity independently of exit-only search settings;
the outer receipt still binds complete actual settings and execution inputs.
Warm-cache, eviction and fresh reconstruction therefore agree. Changed clocks,
cohorts, features and account inputs remain bound. This is an explicit new v9
receipt policy, not a rewrite of v8's saved identities or numerical results.

## Qualification scope

522 distinct scoped backend cases and 243 frontend cases pass, with TypeScript,
scoped lint and a production build. Repeated cases across receipts count once;
the initial identity/reconstruction failures were corrected and pass in the
final integration suites. A completed service run reopens with exactly matching
results and no additional native account calculations.

Tests cover actual 10/60-session math and availability, missing observations,
threshold equality, all new public/prepared daily and minute gate combinations,
cache identity, bounded lifetime, seeded proposals and interrupted recovery.
Golden evidence, result and checkpoint fingerprints prove that the complete
existing v8 synthetic regression remains exact. Service checks retain real
immutable development histories and inspect coverage without reading prices or
running an engine. Complete recovery verifies histories before reuse.

A complete isolated daily run uses actual VectorBT 0.28.5 accounting, Optuna
5.0.0 and native gates over synthetic observed prices, with no broker calls.
It exercises all 192 proposals and explicitly probes every added stock method
in every fit: 204 native accounts complete in 79.79 seconds after native warmup.
The supplied synthetic prices produce no supported improvement, so the original
settings are correctly retained. These timings are isolated observations.
Synthetic prices do not establish a trading edge or certify live
broker latency. The full new minute 192-proposal runtime has not been repeated;
minute gate parity and the existing execution adapter retain their scoped tests.
No live order, trading mode or scheduled strategy is changed.

Resource qualification repeats the expanded prepared gate 100 times with handles
unchanged (1,118 → 1,118), 24,160 bytes of retained tracked allocation and a
765,093-byte peak. Context close, failure/cancellation and garbage collection
release ownership. No second price buffer, global cache, connection, thread or
subprocess is introduced by the numerical expansion.

## Next core work

Continue package 3 with a qualified execution-aware method slice, rather than
adding decorative screens or a broad untested indicator list. Wider EMA windows
need larger warmup admission; ATR-scaled stops/targets need per-entry ATR sizing
and native same-bar execution tests. Volume/liquidity requires observed units and
coverage in the frozen input contract. Then extend the frozen user risk envelope,
portfolio constraints and robust trade-offs under the delivery plan. No method
may consume validation capacity silently or claim a universal future optimum.
