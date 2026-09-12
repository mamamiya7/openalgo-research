import type { PortfolioJob, PortfolioStrategy } from '@/api/portfolioResearch'

export const evaluationDifferenceLabels: Record<string, string> = {
  source: 'Signals',
  cohort: 'Eligible signals',
  observations: 'Price timestamps',
  prices: 'Historical prices',
  calendar: 'Trading calendar',
  instruments: 'Instruments',
  period: 'Evaluation period',
  currency: 'Currency',
  capital: 'Starting capital',
  execution: 'Execution rules',
  costs: 'Costs',
  unverified: 'Unverified report context',
}

export function researchDates(from?: string | null, to?: string | null): string {
  if (!from || !to) return ''
  const start = new Date(`${from.slice(0, 10)}T00:00:00Z`)
  const end = new Date(`${to.slice(0, 10)}T00:00:00Z`)
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start > end) return ''
  const format = new Intl.DateTimeFormat('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  })
  return format.formatRange(start, end).replace(/\s*–\s*/g, '–')
}

const roles = {
  baseline: 'Baseline',
  matched_baseline: 'Matching baseline',
  optimization: 'Optimization study',
  candidate: 'Candidate',
  evaluation: 'Later-period test',
  replay: 'Exact replay',
  backtest: 'Backtest',
}
export function researchResultRole(job: PortfolioJob, report = false): string {
  const context = job.result?.report_context
  const role =
    context?.period === 'evaluation'
      ? 'evaluation'
      : (job.display?.role ?? (job.kind === 'portfolio_optimize' ? 'optimization' : 'backtest'))
  const candidate = context?.candidate ?? job.display?.candidate
  const title = report && role === 'optimization' ? 'Best by objective' : roles[role]
  const trial = candidate?.trial_number
  return `${title}${trial != null && (role !== 'optimization' || report) ? ` · Trial ${trial + 1}` : ''}`
}

export function researchResultPeriod(job: PortfolioJob): string {
  const context = job.result?.report_context
  const basis = context?.evaluation_basis ?? job.result?.evaluation_basis
  // A report may be an embedded later-period result, not the job's primary result.
  // Its evaluated timeline takes precedence over padded calendar coverage or list metadata.
  const recorded =
    basis?.status === 'verified' ? basis.period : (context?.dates ?? job.display?.dates)
  const dates = researchDates(recorded?.from, recorded?.to)
  if (dates) {
    const period =
      basis?.status === 'verified' ? basis.period.kind : (context?.period ?? job.display?.period)
    return `${period === 'selection' ? 'Selection' : period === 'evaluation' ? 'Later period' : 'Tested'} ${dates}`
  }
  const source = job.display?.input_dates
  const input = researchDates(
    source?.from ?? job.source_summary?.date_from,
    source?.to ?? job.source_summary?.date_to
  )
  return input ? `Signals ${input}` : ''
}

export function strategyRuleSummary(strategy: PortfolioStrategy, optimizing = false): string {
  const config = strategy.config
  const value = (
    key: 'target_pct' | 'stop_pct' | 'hold_sessions' | 'hold_minutes' | 'trailing_pct',
    unit = ''
  ) => {
    const range = optimizing ? strategy.search[key] : undefined
    return range ? `${range.min}–${range.max}${unit}` : `${config[key] ?? '—'}${unit}`
  }
  const holding =
    config.hold_minutes != null
      ? `Up to ${value('hold_minutes')} min`
      : config.trade_horizon === 'intraday'
        ? 'Close within day'
        : `Up to ${value('hold_sessions')} sessions`
  return [
    `TP ${value('target_pct', '%')}`,
    `SL ${value('stop_pct', '%')}`,
    holding,
    ...(config.trailing_enabled ? [`Trail ${value('trailing_pct', '%')}`] : []),
  ].join(' · ')
}
