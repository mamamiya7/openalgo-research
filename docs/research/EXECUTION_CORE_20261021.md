# Execution-aware research and portfolio limits — 21 October 2026

This increment extends the existing Research connector. OpenAlgo supplies broker
history, Historify and native ATR; VectorBT still owns orders, fills and account
calculations, and Optuna owns proposal generation. It does not add a general
backtesting engine, live risk manager or broker downloader.

## Native reuse

OpenAlgo already offers [OpenScript chart backtesting](https://www.openalgo.in/script/strategies/backtesting),
[VectorBT backtesting skills](https://docs.openalgo.in/skills/backtesting) and
[execution skills](https://github.com/marketcalls/openalgo-execution-skills).
The latter generate strategy scripts; they are not the Research CSV import,
multi-symbol optimiser and saved-evidence journey. The OpenScript v0.8.1 chart
documentation explicitly excludes filling `exit()` stop/target levels. Indicator
availability alone must not be presented as proof of stop execution parity.
Research reuses the pinned `openalgo.ta.atr` and native VectorBT account runtime.

## Opt-in numerical contract

`automatic-hypotheses-v10` retains the v9 108 stock/market hypotheses and 32
families. It adds finite execution and sizing states, not new entry indicators:

- Percentage exits, or ATR14 stops/targets at registered pairs 1/2, 1/3, 2/3 and
  2/4. ATR uses 100 consecutive completed prior daily OHLC bars and actual entry
  availability; the stop and target are anchored to the actual entry fill.
- Original sizing or reduced allocation and order ceilings, and stop-risk sizing
  targeting half the declared per-trade risk cap within the requested cash budget.
  Reducing allocation and order percentages together is not a claim to halve a
  position. Neither capital nor the user's hard risk limits is an optimiser axis.
- Entry admission limits on gross exposure, aggregate same-symbol exposure,
  independent open lots, per-trade loss to stop and aggregate remaining loss to
  stops. Costs and slippage are included. Whole-share sizes are clipped to all
  applicable cash, allocation and risk limits; no leverage is introduced.

Defaults are explicit for new execution-aware requests: 100% gross exposure,
25% per symbol, 10 open positions, 1% per-trade stop risk and 5% aggregate stop
risk. Existing saved requests acquire no new defaults or numerical rules.
Every strategy in a policy-bearing account must retain its explicit policy and
the same canonical limits. Fixed policies currently require VectorBT.

Limits apply when admitting entries. Appreciation does not force liquidation,
gaps can exceed stops, and OHLC cannot establish intrabar tick ordering. Existing
conservative same-bar stop-first, gap, trailing and scheduled-exit semantics remain
in force. These limits are modelled entry constraints, not guaranteed loss caps.

## Search, validation and recovery

Four independent fits use 64 proposals each (256 total) and a maximum of 384
native calculations. Each fit reserves family and momentum screens, up to four
clock probes, four execution probes and four portfolio probes, leaving at least
16 joint refinement proposals. A missing ATR in any admitted clock excludes the
same source opportunity from every candidate, including the percentage reference.

Three expanding-prefix selections are frozen before testing their next windows.
A final development refit precedes the separately reserved final period. Final
outcomes never rerank settings. Each development winner also retains a matched
original-sizing account with the same filters, exits and hard limits, so changed
sizing is visible separately. This is a development diagnostic, not proof of
sizing alpha or an extra final-period selection opportunity.

The mandate binds risk limits, execution domains and frozen ATR receipt before
scoring. Full development account histories remain bounded and immutable. Resume
verifies them; selected fixed replay retains the original causal feature union
and can use a sliced period without rebuilding it from the selected settings.
Setup reuse retains actual execution policies and risk limits. Read-only method
coverage and Optuna diagnostics understand the new finite domains and budgets.

## Qualification and activation

Qualification passed in isolated databases: 495 distinct backend checks and 230
frontend checks, source compatibility and a production UI build. Receipts are
under ignored `.agent-native/execution-core-20261021/qualification.json` and the
referenced XMLs. A complete real VectorBT/Optuna programme ran 256 proposals and 268
accounts in 247.65 seconds (90.68 seconds in native accounting), using synthetic
prices and real causal gates. It retained the original settings because those
controlled prices supplied no supported improvement. The one final reference
account was reused as the selected final account; final outcomes did not rerank.

Actual VectorBT tests cover daily/minute ATR fills, same-bar ambiguity, gaps,
fees/slippage, duplicate-symbol lots, partial admission and all caps. Independent
review found and fixed two numerical defects: a sizing category that initially
did no distinct work, and simultaneous slipped fills inflating admission equity.
The latter's reproducible 80% gross-cap case now admits 57+19 shares and ends at
79.00% marked exposure, rather than the former 82.25% breach. Selected ATR/stop-risk
follow-up uses Custom search while retaining the actual policy and limits; an
incompatible automatic reference fails before acquisition. Plain fixed percentage
policies do not request ATR history just because they have portfolio limits.

Native gates and seeded Optuna tests cover frozen chronology, durable histories,
pause/resume, completed resume without extra accounting and exact selected replay.
V8/v9 registry/coverage identities and the previous complete native report remain
unchanged. Eight-strategy mandates are bounded at 128 KiB for v10; older mandates
retain 64 KiB. One hundred native accounts kept Windows handles 1011→1011 and showed
an RSS plateau (+10.68MB); a separate traced sample retained 142,503 Python bytes.
One hundred feature attachments kept handles 618→618 and retained 17,292 bytes.
The earlier profiler-instrumented RSS assertion is retained in the ignored audit
receipts; it was characterized separately rather than hidden or treated as a
product resource leak.

Controlled prices/account fixtures do not establish broker acquisition,
profitability or future performance. No broker request or order was used. This
is qualified development source; public release ZIPs remain unchanged.

Local activation of `95d654bae` is verified: the normal account and new built UI
are served at port5000, the worker heartbeat is fresh and the method route still
requires authentication. All63 saved run identities, configuration and Historify
bytes were preserved; no normal research/trading job was started. The same source
commit is pushed to `codex/native-research-refinement`. Rollback source/UI files
are retained under the installation's ignored `.agent-native/execution-core-20261021/before`.

## Remaining scope

This completes a bounded first execution/portfolio increment, not all package4:
liquidity/volume-based capacity, turnover budgets, alternative overlap-priority
search, short/leveraged/derivative execution, minute indicator discovery,
correlation-aware allocation and live regime resizing still need separate data
and numerical admission. The search cannot recover an unknown scanner's rules
or establish a universal optimum.
