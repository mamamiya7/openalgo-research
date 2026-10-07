// Exact copies made by research.analytics._account, not name-based equivalence.
// Sharpe, annualized returns and native engine statistics are intentionally absent.
export const summaryMetricAliases: Readonly<Record<string, string>> = Object.fromEntries(
  [
    'initial_capital',
    'final_equity',
    'net_pnl',
    'net_return_pct',
    'max_drawdown_pct',
    'realized_equity',
    'accepted_trades',
    'closed_trades',
    'pending_trades',
    'unfunded_pending',
    'skipped_trades',
    'excluded_signals',
    'win_rate_pct',
    'profit_factor',
    'sample_adequacy',
  ].map((key) => [`account_${key}`, key])
)

export function summaryMetricKey(key: string): string {
  return Object.hasOwn(summaryMetricAliases, key) ? summaryMetricAliases[key] : key
}

export function savedSummaryMetric(
  summary: Record<string, unknown>,
  metrics: Record<string, unknown>,
  key: string
): unknown {
  const original = summaryMetricKey(key)
  if (Object.hasOwn(summary, original)) return summary[original]
  if (Object.hasOwn(metrics, key)) return metrics[key]
  const copy = `account_${original}`
  return Object.hasOwn(summaryMetricAliases, copy) ? metrics[copy] : undefined
}
