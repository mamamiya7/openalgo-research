# Competing-entry research and filled trading activity — 21 October 2026

## Delivered contract

This second portfolio/execution increment builds on the
[v10 execution core](EXECUTION_CORE_20261021.md). New unconfigured automatic
research uses `automatic-hypotheses-v11`; existing saved recipes retain their
versions. One account-wide choice tests source order, reversed source order and
lower modeled stop-loss fraction first when simultaneous entries compete for
cash. This changes entry admission, not the scanner signals or their histories.

The priority only sorts entry actions at the same observed opening. Opening exits
still precede entries; later exits cannot fund earlier entries. Lower stop risk
uses `(entry cash cost − net protective-stop proceeds) / entry cash cost`, including
fees/slippage and frozen prior ATR where applicable. It cannot inspect the current
bar's high, low, close or future outcomes. Source index breaks ties; invalid
protection sorts last and rejects only the affected entry. All existing sizing,
cash and hard portfolio limits apply after ordering. Source order spans strategy
order then CSV row order; reversed source reverses the whole simultaneous batch.

The explicit `research-execution-aware-v2` policy and native adapter/policy v3
identify this behavior. Every strategy must share the same account-wide priority
and risk limits. Saved v1 policies and v10 reports retain their numerical identities.
Starting a new v11 study from an eligible percentage/allocation setup explicitly
creates the new reference policy; selected ATR, stop-risk or non-source settings
instead retain their actual settings through Custom search.

## Bounded automatic programme

The 108 hypotheses across 32 method families remain unchanged. A single global
`portfolio.entry_priority` domain adds three choices and two mandatory probes,
not a Cartesian product per CSV. Each of four independent training fits has 68
proposals: 272 total, with a 384-native-calculation ceiling. The conservative bound
including controls, validation, reconstruction, final confirmation and recovery
is 359. Screening leaves at least 18 refinement proposals per fit.

Matched original-sizing development controls retain the candidate's priority.
Final-period results remain separate and cannot rerank settings. Coverage and
diagnostics show actual per-fit priority proposals, evaluated accounts and reuse.
Only proposal-linked training accounts count toward this coverage.

No additional price stream or indicator is acquired for entry priority. Plain
fixed percentage/capped accounts still avoid unnecessary ATR history.

## Actual filled trading activity

Native v3 reports derive `research-execution-activity-v1` directly from VectorBT
order records: buy/sell notional, total traded value, fees, entry/exit fill counts
and daily rows. Notional uses actual filled size and slipped fill price, excludes
fees and counts each buy and sell once. Open entries and same-bar round trips are
included; unfilled signals are excluded. The ratio divides total traded notional
by original starting capital. It is not annualised turnover or proof of liquidity,
market capacity, spread or impact. Existing v1/v2 reports are not rewritten.

The entry ledger additionally records the actual priority, rank, batch size and
modeled stop-risk fraction, including rejected entries. Reports expose compact
filled-value and fee totals without adding a separate configuration journey.

## Journey integrity

Adding another CSV inherits account-wide limits and priority without copying the
other strategy's ATR rules. Recapturing a scanner preserves its saved allocation,
execution policy and limits. Saved selected settings, exact replay, resume and
diagnostics understand the new version; incompatible frozen versions fail before
acquisition rather than silently changing semantics.

## Qualification

Isolated tests cover actual VectorBT priorities, cash competition, fees, caps,
same-bar activity, daily/minute fills, causal inputs, frozen versions, selected
replay, durable pause/resume, Chartink recapture and the native settings journey.
The final frontend selection passes 235 tests; TypeScript, production build,
Python lint and source compatibility pass. Detailed backend receipts are retained
under ignored `.agent-native/priority-core-20261021/` and
`.agent-native/priority-native-20261021/`.

A complete real VectorBT 0.28.5 / Optuna 5.0.0 programme evaluates all three
priorities across 272 proposals and 284 native accounts in 265.34 seconds
(95.03 seconds in native accounting), using synthetic prices and real causal
gates. It correctly retains the reference when these fixtures offer no supported
improvement. A separate service test verifies saved recovery and selected replay.
Full v10 result, state, evidence and registry hashes match the prior implementation.
One hundred native accounts keep Windows handles at 1006 and show a stable RSS
plateau, with 520,192 bytes net growth across the measured run.

No broker request, order, normal research job or trading job was started for this
qualification. Controlled fixtures do not establish profitability or live broker
performance. Public release ZIPs remain unchanged.

Local activation of `9f384f959` is verified at port 5000: the existing account,
new served UI, authenticated method route and fresh calculation-worker heartbeat
pass, as does installed source compatibility. All 63 saved run identities,
configuration and Historify bytes are unchanged. The same code is pushed to
`codex/native-research-refinement`. Rollback source/UI files are retained under
the installation's ignored `.agent-native/priority-core-20261021/before`.

## Next core work

Continue package 4 with an explicit entry turnover budget using actual buy fills
and a frozen daily opening-equity denominator; protective exits must never be
blocked. Volume/capacity and correlation-aware allocation require their own data
and accounting admission. Then build the package 5 recommendation/uncertainty
controller on the saved development evidence before wider presentation polish.
These are remaining capabilities, not claims of this increment.
