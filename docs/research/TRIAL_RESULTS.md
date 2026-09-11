# Reading optimization trials

Updated 11 September 2026. The native Research portfolio workflow now includes the [full tear-sheet analysis](TEARSHEET_PLAN.md).

## What the user sees

**View settings** opens the exact trial in a bounded dialog. It starts with focus on its title, works with multiple strategies, and returns focus to the same row on closing. Opening settings never launches a calculation. **Backtest this** remains an explicit action for that candidate.

The table shows 25 distinct configurations per page, keeps its header and trial identity visible during scrolling, and retains its page when switching result tabs. Repeated Optuna proposals are reported separately from distinct evaluated configurations.

**Columns** opens a small chooser with Apply, Cancel and Reset to default. The default columns are Return, Max drawdown, Win rate, Profit factor and Closed trades. Choices are stored per account in this browser, not inside immutable research evidence. Older studies show only fields they actually saved. Missing, null or non-finite values display `—`; real zero remains zero.

## Calculated versus displayed

Both portfolio engines retain their unchanged shared `_summary` and separate Optuna ranking score. There are **15 original summary fields: 14 numeric values/counts and one sample assessment**, plus the separate objective score. New evaluations also save a versioned scalar analysis on every distinct trial. The Columns chooser accepts the shared native metric catalog as well as these original fields.

| Available field | Meaning |
| --- | --- |
| Return | Marked equity change over the tested period, not annualized |
| Max drawdown | Largest percentage fall from the running equity peak |
| Win rate | Profitable closed trades divided by all closed trades |
| Profit factor | Closed-trade profits divided by losses; `—` when losses are zero |
| Net P&L | Final marked equity minus starting capital, including open positions |
| Objective score | Saved Optuna ranking score for the chosen objective; higher ranks first |
| Final equity | Ending cash plus marked open positions |
| Starting capital | Shared initial capital |
| Realized equity | Starting capital plus closed-trade P&L; not available cash |
| Closed trades | Completed exits |
| Entered trades | Positive-quantity trades, closed or still open |
| Open trades | Entered trades still pending at the end |
| Pending entries | Pending signals with no entered quantity yet; not automatically insufficient funds |
| Skipped trades | Entries skipped under the recorded rules |
| Excluded signals | Exclusions under the saved data/calendar policy |
| Sample assessment | The existing fewer-than-30-closed-trades flag; not proof of statistical robustness |

Currency values use INR. Percentages are already stored in percentage units. The account's open positions participate in marked return/drawdown, while win rate and profit factor use closed trades. These bases intentionally differ and must not be conflated.

The analysis catalog covers **121 VectorBT metrics** (27 common account metrics plus all 94 entries in its Portfolio, Returns, Trades and Drawdowns registries) or **88 Nautilus metrics** (27 common plus 61 entries from all 34 native statistic classes, split by account/position basis). Definitions, sources and unavailable-value reasons travel with the analysis. Values include Sharpe, Sortino, annualized return/volatility, Calmar, Omega, drawdown durations, trade statistics and historical tail-risk statistics. Applicability depends on the recorded evidence and sample; catalog coverage is not a promise that every statistic is numerically defined.

**Tear sheet** has interactive account/return/risk/trade charts and frozen-price candles with recorded entry/exit markers. **Study analysis** uses Optuna's native plots. Native-only values absent from older reports require an explicit replay; supported old account/trade analysis can be prepared from saved evidence without broker downloads. The original result is never rewritten.

Only the selected winner retains its full curves, available cash, detailed ledger and strategy attribution. Those winner-only values must not be copied into other trial rows. Computing additional historical metrics for old non-winning trials can require explicit replay because their full curves/ledgers were not retained.

Displaying the already saved fields needs no broker request, price download, new trial or change to existing evidence.
