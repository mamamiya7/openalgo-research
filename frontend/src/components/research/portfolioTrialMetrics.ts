import type { AnalysisMetric, PortfolioTrial } from '@/api/portfolioResearch'

type Metric = Omit<AnalysisMetric, 'source'> & { source?: string }

// These are stored summary fields shared by the VectorBT and Nautilus adapters.
// Do not derive winner-only values from the selected report for other trials.
export const trialMetrics: readonly Metric[] = [
  {
    key: 'net_return_pct',
    label: 'Return',
    group: 'Performance',
    format: 'percent',
    description: 'Change in marked portfolio equity, including open positions.',
  },
  {
    key: 'max_drawdown_pct',
    label: 'Max drawdown',
    group: 'Performance',
    format: 'percent',
    description: 'Largest peak-to-trough decline in marked equity.',
  },
  {
    key: 'win_rate_pct',
    label: 'Win rate',
    group: 'Performance',
    format: 'percent',
    description: 'Profitable closed trades as a share of all closed trades; — if none closed.',
  },
  {
    key: 'profit_factor',
    label: 'Profit factor',
    group: 'Performance',
    format: 'number',
    description: 'Profits divided by losses on closed trades; — when no losses were recorded.',
  },
  {
    key: 'net_pnl',
    label: 'Net P&L',
    group: 'Performance',
    format: 'money',
    description: 'Final marked equity minus starting capital, including open positions.',
  },
  {
    key: 'objective_score',
    label: 'Objective score',
    group: 'Performance',
    format: 'number',
    description:
      'The saved optimization score used to rank trials. Higher is better for the chosen objective.',
  },
  {
    key: 'final_equity',
    label: 'Final equity',
    group: 'Account',
    format: 'money',
    description: 'Cash plus the marked value of open positions at the end.',
  },
  {
    key: 'initial_capital',
    label: 'Starting capital',
    group: 'Account',
    format: 'money',
    description: 'Shared starting cash for the tested portfolio.',
  },
  {
    key: 'realized_equity',
    label: 'Realized equity',
    group: 'Account',
    format: 'money',
    description: 'Starting capital plus closed-trade P&L; excludes open-position P&L.',
  },
  {
    key: 'closed_trades',
    label: 'Closed trades',
    group: 'Trades',
    format: 'number',
    description: 'Trades with a recorded completed exit.',
  },
  {
    key: 'accepted_trades',
    label: 'Entered trades',
    group: 'Trades',
    format: 'number',
    description: 'Trades assigned a positive quantity, including positions still open.',
  },
  {
    key: 'pending_trades',
    label: 'Open trades',
    group: 'Trades',
    format: 'number',
    description: 'Funded trades still pending at the end of the recorded period.',
  },
  {
    key: 'unfunded_pending',
    label: 'Pending entries',
    group: 'Trades',
    format: 'number',
    description: 'Pending signals with no funded quantity yet.',
  },
  {
    key: 'skipped_trades',
    label: 'Skipped trades',
    group: 'Trades',
    format: 'number',
    description: 'Trades skipped under the recorded portfolio rules.',
  },
  {
    key: 'excluded_signals',
    label: 'Excluded signals',
    group: 'Trades',
    format: 'number',
    description: 'Signals excluded under the recorded data or calendar policy.',
  },
  {
    key: 'sample_adequacy',
    label: 'Sample assessment',
    group: 'Trades',
    format: 'text',
    description:
      'Fewer than 30 closed trades is flagged as an insufficient sample; this is not a performance guarantee.',
  },
]

export const defaultTrialColumns = [
  'net_return_pct',
  'max_drawdown_pct',
  'win_rate_pct',
  'profit_factor',
  'closed_trades',
]

export function trialMetricText(metric: Metric, value: unknown): string {
  if (metric.format === 'text') return typeof value === 'string' && value ? value : '—'
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  if (metric.format === 'money') {
    return value.toLocaleString('en-IN', { style: 'currency', currency: 'INR' })
  }
  return `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${metric.format === 'percent' ? '%' : ''}`
}

export function availableTrialMetrics(rows: PortfolioTrial[], catalog: AnalysisMetric[] = []) {
  const known = new Set(trialMetrics.map((metric) => metric.key))
  return [...trialMetrics, ...catalog.filter((metric) => !known.has(metric.key))].filter((metric) =>
    rows.some((row) =>
      metric.key === 'objective_score'
        ? Object.hasOwn(row, 'score')
        : Object.hasOwn(row.summary, metric.key) ||
          Object.hasOwn(row.analysis?.metrics ?? {}, metric.key)
    )
  )
}

export function trialMetricValue(metric: Metric, row: PortfolioTrial) {
  return metric.key === 'objective_score'
    ? row.score
    : Object.hasOwn(row.summary, metric.key)
      ? row.summary[metric.key]
      : row.analysis?.metrics[metric.key]
}

export function readTrialColumns(owner: string, catalog: AnalysisMetric[] = []): string[] {
  try {
    const text = localStorage.getItem(`research-trial-columns:v1:${owner}`)
    if (text && text.length < 32768) {
      const value: unknown = JSON.parse(text)
      if (Array.isArray(value)) {
        const known = [...trialMetrics, ...catalog]
          .filter((metric) => value.includes(metric.key))
          .map((metric) => metric.key)
        if (known.length) return known
      }
    }
  } catch {
    /* Table remains usable when browser storage is unavailable. */
  }
  return [...defaultTrialColumns]
}

export function writeTrialColumns(owner: string, columns: string[]) {
  try {
    localStorage.setItem(`research-trial-columns:v1:${owner}`, JSON.stringify(columns))
  } catch {
    /* Keep the selected columns in memory for this visit. */
  }
}
