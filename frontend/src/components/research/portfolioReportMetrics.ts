import type { AnalysisMetric, PortfolioResult } from '@/api/portfolioResearch'
import { trialMetricText } from './portfolioTrialMetrics'

export type ReportMetric = AnalysisMetric & { summaryKey?: string }

// Curated account keys are explicit. Native engine metrics with similar labels
// remain in their provider catalog; they are never substituted for these values.
export const reportMetrics: readonly ReportMetric[] = [
  {
    key: 'account_net_return_pct',
    summaryKey: 'net_return_pct',
    label: 'Net return',
    group: 'Performance',
    format: 'percent',
    description: 'Change in marked account equity, including open positions.',
    source: 'Shared account',
  },
  {
    key: 'account_net_pnl',
    summaryKey: 'net_pnl',
    label: 'Net P&L',
    group: 'Performance',
    format: 'money',
    description: 'Final marked equity minus starting capital, including open positions.',
    source: 'Shared account',
  },
  {
    key: 'account_max_drawdown_pct',
    summaryKey: 'max_drawdown_pct',
    label: 'Max drawdown',
    group: 'Risk',
    format: 'percent',
    description:
      'Largest decline from a prior peak across saved account marks, including intraday marks when present.',
    source: 'Shared account',
  },
  {
    key: 'account_sharpe_ratio',
    label: 'Sharpe',
    group: 'Risk',
    format: 'number',
    description:
      'Daily account return mean divided by sample volatility, annualized over 252 sessions; risk-free return 0.',
    source: 'Shared account',
  },
  {
    key: 'account_win_rate_pct',
    summaryKey: 'win_rate_pct',
    label: 'Closed-trade win rate',
    group: 'Trades',
    format: 'percent',
    description:
      'Profitable closed trades as a share of closed trades. Undefined when none have closed.',
    source: 'Shared account',
  },
  {
    key: 'account_closed_trades',
    summaryKey: 'closed_trades',
    label: 'Closed trades',
    group: 'Trades',
    format: 'number',
    description: 'Trades with a recorded completed exit.',
    source: 'Shared account',
  },
  {
    key: 'account_initial_capital',
    summaryKey: 'initial_capital',
    label: 'Starting capital',
    group: 'Performance',
    format: 'money',
    description: 'Shared starting cash for this evaluation period.',
    source: 'Shared account',
  },
  {
    key: 'account_final_equity',
    summaryKey: 'final_equity',
    label: 'Final equity',
    group: 'Performance',
    format: 'money',
    description: 'Cash plus the marked value of open positions at the end.',
    source: 'Shared account',
  },
  {
    key: 'account_annualized_return_pct',
    label: 'Annualized return',
    group: 'Performance',
    format: 'percent',
    description: 'Compounded return annualized over 252 recorded sessions; not calendar-year CAGR.',
    source: 'Shared account',
  },
  {
    key: 'account_sortino_ratio',
    label: 'Sortino',
    group: 'Risk',
    format: 'number',
    description:
      'Daily account return mean divided by downside root-mean-square, annualized over 252 sessions; required return 0.',
    source: 'Shared account',
  },
  {
    key: 'account_annualized_volatility_pct',
    label: 'Annualized volatility',
    group: 'Risk',
    format: 'percent',
    description:
      'Sample standard deviation of daily account returns multiplied by the square root of 252.',
    source: 'Shared account',
  },
  {
    key: 'account_profit_factor',
    summaryKey: 'profit_factor',
    label: 'Profit factor',
    group: 'Trades',
    format: 'number',
    description:
      'Closed-trade profits divided by losses. Undefined when there are no recorded losses.',
    source: 'Shared account',
  },
  {
    key: 'account_trade_expectancy',
    label: 'Average trade P&L',
    group: 'Trades',
    format: 'money',
    description: 'Average recorded net P&L of closed trades.',
    source: 'Shared account',
  },
  {
    key: 'account_available_cash',
    label: 'Available cash',
    group: 'Capital',
    format: 'money',
    description: 'Cash at the final saved account mark.',
    source: 'Shared account',
  },
  {
    key: 'account_recorded_fees',
    label: 'Recorded fees',
    group: 'Capital',
    format: 'money',
    description: 'Fees present in the saved ledger, including entered open positions.',
    source: 'Shared account',
  },
]

export function availableReportMetrics(result: PortfolioResult): ReportMetric[] {
  const curated = new Set(reportMetrics.map((metric) => metric.key))
  return [
    ...reportMetrics,
    ...(result.analysis?.catalog ?? []).filter((metric) => !curated.has(metric.key)),
  ]
}

export function reportMetricLabel(metric: ReportMetric) {
  return reportMetrics.some((item) => item.key === metric.key)
    ? metric.label
    : `${metric.label} · ${metric.source ?? metric.group}`
}

export function reportMetricValue(result: PortfolioResult, metric: ReportMetric) {
  if (metric.summaryKey && Object.hasOwn(result.summary, metric.summaryKey)) {
    return result.summary[metric.summaryKey]
  }
  if (Object.hasOwn(result.analysis?.metrics ?? {}, metric.key))
    return result.analysis?.metrics[metric.key]
  if (metric.key === 'account_available_cash') return result.equity_curve.at(-1)?.cash
  return undefined
}

export function reportMetricText(
  result: PortfolioResult,
  metric: ReportMetric,
  currency: string | null = 'INR'
) {
  return trialMetricText(metric, reportMetricValue(result, metric), currency)
}

export function reportMetricDescription(result: PortfolioResult, metric: ReportMetric) {
  const definition = result.analysis?.catalog.find((item) => item.key === metric.key)?.description
  const unavailable = result.analysis?.unavailable[metric.key]
  return [definition || metric.description, unavailable].filter(Boolean).join(' ')
}
