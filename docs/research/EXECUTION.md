# Scanner Research execution policy

Policies: `scanner-minute-causal-v1` for minute snapshots and retained
`scanner-eod-next-open-marked-v3` for daily snapshots. Metric basis: `daily_marked`.
Experiment semantics: `scanner-experiments-v2`.

New native inputs record price policy `openalgo-native-history-v1`. The numerical
daily/minute execution rules below are unchanged; the source now uses provider-native
prices and OpenAlgo's calendar, with no extra public adjustment or price-match gate.
Consequently new results may differ from earlier reference-adjusted results even
for the same CSV/settings. Saved results retain their original exact evidence.

Version 2 added causal scanner-count triggers, explicit trailing state, exposure
limits and deterministic priorities. Version 3 corrects later-window scanner-count
warmup; experiment semantics v2 correct cumulative follow-ups, categorical
neighborhoods and explicit trailing-grid states. Saved version 1/2 artifacts
remain unchanged and retain their original interpretations. Missing `trailing_enabled` on a version 1-style request means enabled
when `trailing_pct > 0`; explicit enabled-zero remains a distinct configuration
and places the initial trailing stop at the slipped entry price.

The shared fixed-setup, optimization and research evaluator uses the financial requirements in `BRIEF.md` and the
read-only Backtest Engine reference at commit `ff86bf1`, version 10.18,
`docs/product.md`, `services/execution.py`, and `tools/test_execution_contract.py`.
It does not import or run that application. OpenAlgo's live `services/risk/`
evaluates actual quote ticks and order-facing risk; historical daily-bar ordering,
cash funding, and incomplete-candle uncertainty require this separate pure
calculation policy.

## Daily/minute execution and automatic planning

Date-only CSVs with session-based holding select the existing
`scanner-eod-next-open-marked-v3` evaluator and daily candles. Stops, targets and
trailing use the daily OHLC rules below; they do not by themselves request minute
data. In particular, daily bars cannot reveal within-day stop/target order, so
the recorded conservative stop-first assumption remains explicit in the saved
policy. The previous planner unnecessarily forced defaults to minute prices;
correcting selection does not change either evaluator or existing saved reports.

Timestamps or explicit intraday timing select `scanner-minute-causal-v1`.

Minute snapshots use aware Asia/Kolkata minute-open timestamps and the explicit
expected exchange grid, including slots whose observations are missing. Date-only
signals enter the following session; timestamped signals cannot enter at or before
their observation. Configured entry clocks, exit clocks, minute holding limits and
intraday versus multiday horizon are shared by fixed evaluation, optimizer and
research. The planner includes the holding-window union of search extrema and
sensitivity variants before acquisition; it never downloads for every candidate.

Opening gaps fill at the observed minute open. Within one minute, unresolved
stop/target ordering remains stop-first. Trailing changes use completed bars.
Intraday flattening requires the actual scheduled closing minute; a truncated
tail cannot masquerade as session end. Missing required observations preserve
pending outcomes rather than advancing to the next available candle. Minute
execution still reports daily marked portfolio metrics. Daily evidence is never
expanded into invented minute candles.

The following daily timing rules apply to new EOD and retained daily snapshots.
Previously saved results and their exact policy identifiers are unchanged.

## Daily timing and funding

- Every dated signal observes the session close and enters at the next session
  in the snapshot's explicit calendar. Missing entry candles cannot shift entry
  to a later available quote. Signals outside that calendar are excluded.
- Eligibility follows the configured scanner-count trigger modes below. Source
  CSV, alphabetical, reversed CSV or per-session seeded shuffle order resolves
  simultaneous candidates. Repeated signals can create separate funded lots. Duplicate
  dated-symbol CSV records retain only their first occurrence.
- The daily opening portfolio value determines each requested percentage
  budget. Strict sizing requires that full requested budget in both cash and
  remaining exposure allowance. Remaining sizing uses the smallest of requested
  budget, available cash and remaining exposure allowance. Whole shares include
  entry fees, with no leverage. Existing exposure is marked at the opening price
  (last mark if missing), capped as a percentage of opening portfolio equity.
  New lots consume their cost including fees for the rest of that opening batch.
- Existing positions that exit at the opening price release cash before new
  orders. Intraday and closing exits cannot finance that morning's entries.
- Stops and targets apply on entry day. Gaps through either level fill at the
  observed open. Otherwise a daily candle touching both exits hits the stop
  first. Completed highs can raise the trailing stop for the following bar.
- Holding duration counts elapsed exchange sessions after entry. A five-session
  hold exits at the close five sessions after entry unless an earlier stop or
  target resolves the position.
- An intervening missing candle makes an accepted position's exit permanently
  uncertain within the snapshot. Later bars can supply marks but cannot prove
  that an unseen stop was avoided. Unfunded missing-entry candidates are counted
  separately from accepted pending positions.

## Fees: intentional difference from the reference

The reference folds fees into rounded two-decimal per-share buy/sell prices and
anchors stops/targets on that fee-inclusive buy price. This policy separates
slipped execution price from explicit cash fees. Each side's fee is charged on
its slipped traded notional. Stops/targets anchor to slipped entry price,
excluding fees, and their levels are rounded to two decimals. Internal cash
calculations retain full precision; reported cash values use six decimals.

For a raw entry of 33.33, 10 bps slippage, 10 bps fee and 5.1% target, the reference
records a fee-inclusive buy of 33.40, target of 35.10 and net sale of 35.03. The new
policy records entry 33.36333, target 35.06, slipped sale 35.02494, and fees
separately. This deliberately changes trigger levels and numerical P&L in such
cases. `test_intentional_legacy_fee_anchor_and_rounding_difference` fixes the
expected calculation. No archived result is reinterpreted or recalculated.

## Metrics and data limits

Daily equity is cash plus open whole shares marked at the close. Missing closes
carry the last known mark with explicit missing-mark evidence. Drawdown begins
from initial capital. Realized equity is a separate diagnostic, and positions
are not forcibly liquidated at the snapshot end. Win rate and profit factor use
accepted completed trades only; a missing losing denominator gives a null
profit factor. Fewer than 30 completed trades is labeled insufficient sample;
30 trades is a presentation guardrail, not statistical assurance.

Fixtures are deterministic synthetic OHLC and a synthetic weekday calendar.
They are never represented as exchange prices or a verified NSE session list.
Historify's current archive schema lacks provider/adjustment/ISIN lineage and a
verified exchange calendar. Its read-only row counts and timestamp ranges are
inspectable observations, but calculation remains blocked. The checked snapshot
boundary requires this provenance before accepting non-synthetic series.

CSV intake is limited to 8 MiB, 25,000 rows and ten years. Hold duration supports
1 through 252 elapsed exchange sessions. Snapshot limits are enforced by the
data boundary. Every run retains normalized input,
configuration, snapshot, coverage and policy identity through the persistence
layer. No point-in-time scanner membership, liquidity/capacity, delivery or
settlement eligibility, or corporate-action entitlement cashflows are inferred.

Numerical tests cover exact next-session entry, entry-day stop-first, opening
gaps, delayed trailing updates, elapsed hold duration, cash sequencing, priority,
fee/slippage P&L, pending drawdown, missing entries and intervening candles,
snapshot validation, deterministic fixtures and cancellation callbacks.


## Causal trigger modes and complete configuration identity

Scanner counts count normalized dated-symbol signals on each explicit snapshot
session, including zero-count sessions. Bottom Fishing requires seven prior
sessions, a current count strictly between zero and three, and a count strictly
below half the mean of those seven prior counts. Zero Only requires a zero prior
count and a positive current count. Uptick requires a prior Bottom Fishing trough
or zero count and a current count larger than that prior count. Each qualifying
signal still enters the following session open. No future count participates.

Selected filters combine with OR semantics. Bypass overrides all filters; the
canonical list becomes only Bypass. Otherwise modes are stored in Bottom Fishing,
Zero Only, Uptick order without duplicates, giving exactly eight meaningful mode
strategies. A fold can pass complete historical scanner signals as
`signal_history` while its `signals` argument contains only eligible entries;
history warms triggers without trading earlier candidates. Counts outside the
snapshot session calendar do not contribute.

Every canonical configuration retains initial capital, order size percentage,
target/stop percentages, hold sessions, fee/slippage basis points, modes,
`trailing_enabled`, trailing percentage, entry priority, signed 32-bit priority
seed, maximum exposure percentage and strict/remaining fill policy. Seeded
shuffle uses a local RNG seeded by `seed:entry-session-date`; it neither mutates
source CSV order nor consumes shared random state. A disabled trailing setting
retains its supplied percentage for exact identity, although it does not trail.

`convert_legacy_config` accepts the explicit source version
`eod_next_open_marked_v2`. It converts old fractional targets/stops, sizing,
exposure and trailing values into percentages, and fractional slippage into basis
points. Existing cost basis points retain their units. Source priorities and fill
policies map to canonical native values. Explicit legacy enabled-zero trailing
is preserved. The conversion creates a new native configuration; it does not
promise legacy fee/price parity or mutate legacy results.

`prepare_evaluation` validates immutable inputs and computes scanner-count history
once per bounded job. Every grid row, full report and summary-only run uses the
same financial loop. Summary-only runs omit ledger/curve/mark detail in the
returned report while retaining the identical summary and configuration. The
prepared object is local to its job, owns no I/O resources and is not retained in
a module-level cache. Mutating inputs during its lifetime is unsupported.

A prior policy-v2 isolated computation measurement on Windows 11/Python 3.12.10 used 18,310 synthetic
signals, 150 symbols, 130 explicit synthetic sessions and 19,500 bars: preparation
0.016 seconds, one full evaluation 0.036 seconds, and 100 summary configurations
3.63 seconds. The full JSON report was about 8.0 MB. These are synthetic engine
measurements, not a real-data benchmark, a 3,000-row search guarantee, or evidence
of worker/API health latency.

## Search and chronological evaluation

Quick/full sample evenly inside a bounded grid. Auto takes a broad pass then
focuses on untested grid neighbors of leading settings; earlier-only selection
uses the same planner. Candidate ranking is explicitly net return, negative
maximum drawdown, or net return minus maximum drawdown. Nearby-setting statistics
use evaluated points, not a continuous-region robustness claim. The native
sample adequacy heuristic (30 signals and 30 closed trades) is an explicit
heuristic, not statistical significance.

Earlier training snapshots cut both prices and corporate-action overlays at
the recorded cutoff. Training alone chooses a setup; later prices cannot change
its ranking. Fixed-setup intent does not use training eligibility to replace or
reject the supplied setup. Each disjoint later window starts with fresh capital,
retains pending positions at its final mark and has its own curve. Eight prior
calendar sessions warm scanner counts: first-day Uptick reads yesterday's
Bottom Fishing flag, which itself needs seven earlier counts; the initial flat rows are labelled in
the chart, and absent scanner observations are a zero-count assumption. Research
summary returns are means of independent fold returns, not a funded portfolio
return. Sensitivity changes named assumptions without selecting a replacement.

All completed investigation kinds record explored date/symbol overlap for later
reports. This is a record of known reuse within the store, not proof of unseen
data or point-in-time scanner membership. Shared prepared calculations are local
to a bounded job. Checkpoints retain completed results and the exact source,
configuration, search specification and policy identity before explicit resume.

The external 18,310-signal / 3,000-configuration public-price benchmark and its
resource/HTTP measurements are recorded in [VERIFICATION.md](VERIFICATION.md).


## Audit corrections: A2, A3, A4 and A8

Later eligibility now retains eight previous scanner sessions, without trading
warmup signals or requiring pre-signal stock candles. The first-window Uptick
reproducer with seven prior counts of six, then a trough count of two and a
current count of three previously admitted zero first-day trades; it now admits
three, matching full causal history restricted to the same eligible signal dates.
All eight mode combinations, zero/trough boundaries, gaps and multiple folds are
covered by comparison tests. Existing future-signal/price/action mutation tests
continue to protect earlier-only selection.

A follow-up investigation requires the complete verified parent configuration
rows and exact saved selected reports. The service verifies the owner, source,
policy, experiment identity and artifact chain; `run_search` checks grid/config
identity, ranking scores and the exact distinct exclusion union. Cumulative rows
are ranked without recalculating earlier summaries. A retained incumbent or
alternative uses its saved parent report; a missing report fails closed. New
reports expose cumulative `rows`, `selected_reports` and neighborhood statistics,
plus separate `pass_rows`, pass recommendation and pass-specific counts.

A worse follow-up therefore cannot erase a positive incumbent or earlier
neighbors. In the regression, the first pass finds +3.621172% with 40 closed
trades, while the second tests -0.392%; the cumulative recommendation retains the
first result. Untested distinct settings remain available for an explicit
follow-up regardless of the current winner's sign or sample label. A completed
pass is not a claim that every grid point was explored. Once the grid is
exhausted, no further identical follow-up is offered. Earlier-only training
searches cannot import exclusions learned from a full-period investigation.

Checkpoints retain only the current pass's completed rows and plan, alongside a
fingerprint of the inherited evidence and the experiment version. Resume checks
that fingerprint and the exact planned configuration identities. Old experiment
checkpoints do not resume under corrected semantics; saved completed evidence
remains reviewable. Parent artifact references belong to the persistence/export
closure, not to a silent numerical reconstruction of prior work.

Numerical neighborhoods use adjacent target/stop/hold steps, and enabled trailing
magnitude steps, **within the same trigger strategy and trailing enabled state**.
Disabled trailing percentages are inactive identity metadata and cannot inflate
neighbor counts. Other trigger/trailing states at the same target/stop/hold are
listed separately as categorical comparisons; they are not numerical robustness
evidence. Findings describe the observed profitable-neighbor fraction rather
than claiming a stable parameter region. Focused search uses this same distance.
Canonical category ordering makes broad and focused plans, configuration-keyed
neighbor membership, medians and findings invariant to supplied category order.

Search specifications explicitly retain `trailing_choices`, each an `enabled`
boolean and `pct` value. With no trailing axis, the exact baseline pair is kept,
including enabled-zero and disabled-positive. An explicit positive-valued numeric
axis requests enabled trailing; a zero axis value preserves enabled-zero when
that is the explicitly enabled-zero baseline, otherwise it means disabled zero.
Callers can supply state/value pairs directly to avoid inferred axis meaning.
Including off adds the real `(false, 0)` baseline. Excluding off while requesting
a disabled pair is rejected before submission. Normalized preflight and execution
replay the same choices; no enabled-zero request silently becomes disabled.

The durable audit regressions are in `test/research/test_audit_math.py`. They also
cover better/worse/tied multigeneration investigations, cumulative neighbor
retention, no recalculation of inherited reports, interrupted follow-ups,
checkpoint conflicts, category permutations, adaptive-selection invariance and
the explicit trailing state/value matrix. These corrections do not alter the
previously documented explicit fee accounting or daily marked-equity arithmetic.
