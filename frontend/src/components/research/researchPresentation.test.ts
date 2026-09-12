import { describe, expect, it } from 'vitest'
import type { PortfolioJob, ResearchResultDescriptor } from '@/api/portfolioResearch'
import { freshPortfolioDraft } from './PortfolioBuilder'
import {
  researchDates,
  researchResultPeriod,
  researchResultRole,
  strategyRuleSummary,
} from './researchPresentation'

const job: PortfolioJob = {
  id: 'job',
  status: 'completed',
  progress: 1,
  created_at: 1,
  kind: 'portfolio_backtest',
}
describe('recognizable research evidence', () => {
  it('keeps legacy input dates distinct from unknown tested dates and origin', () => {
    const legacy = { ...job, source_summary: { date_from: '2026-01-02', date_to: '2026-02-03' } }
    expect(researchResultRole(legacy)).toBe('Backtest')
    expect(researchResultPeriod(legacy)).toMatch(/^Signals /)
    expect(researchResultPeriod(job)).toBe('')
    expect(researchDates('unknown', '2026-01-02')).toBe('')
  })
  it('shows the actual role, human trial number and recorded period', () => {
    const candidate = {
      ...job,
      display: {
        role: 'evaluation',
        period: 'evaluation',
        dates: { from: '2026-04-01', to: '2026-06-30', status: 'recorded' },
        candidate: { study_job_id: 'study', config_id: 'exact-config', trial_number: 14 },
      } as ResearchResultDescriptor,
    }
    expect(researchResultRole(candidate)).toBe('Later-period test · Trial 15')
    expect(researchResultPeriod(candidate)).toMatch(/^Later period /)
    expect(researchResultPeriod(candidate)).toContain('2026')
  })
  it('does not present the optimizer leader as a human chosen setup', () => {
    const study = { ...job, kind: 'portfolio_optimize' }
    expect(researchResultRole(study)).toBe('Optimization study')
    expect(researchResultRole(study, true)).toBe('Best by objective')
  })
  it('uses the displayed result timeline instead of padded calendar or parent-study dates', () => {
    const later = {
      ...job,
      display: { period: 'selection', dates: { from: '2026-01-05', to: '2026-06-30' } },
      result: {
        report_context: {
          period: 'evaluation',
          dates: { from: '2026-07-01', to: '2026-09-10' },
          evaluation_basis: {
            status: 'verified',
            period: {
              kind: 'evaluation',
              from: '2026-08-03T09:15:00+05:30',
              to: '2026-08-04T10:30:00+05:30',
            },
          },
        },
      },
    } as unknown as PortfolioJob
    expect(researchResultPeriod(later)).toBe(
      `Later period ${researchDates('2026-08-03', '2026-08-04')}`
    )
  })
  it('shows varying ranges and intraday holding without choosing candle data', () => {
    const draft = freshPortfolioDraft()
    const strategy = {
      id: 'one',
      name: 'Signals',
      type: 'signals' as const,
      source_id: 'source',
      allocation_pct: 100,
      config: {
        initial_capital: draft.portfolio.capital,
        target_pct: 10,
        stop_pct: 5,
        hold_sessions: 5,
        hold_minutes: 60,
        trailing_enabled: true,
        trailing_pct: 2,
      },
      search: { target_pct: { min: 5, max: 15, step: 5 } },
    } as Parameters<typeof strategyRuleSummary>[0]
    expect(strategyRuleSummary(strategy, true)).toBe('TP 5–15% · SL 5% · Up to 60 min · Trail 2%')
    expect(strategyRuleSummary(strategy)).toContain('TP 10%')
  })
})
