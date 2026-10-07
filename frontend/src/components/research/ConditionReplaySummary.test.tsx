import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import type { PortfolioResult } from '@/api/portfolioResearch'
import { ConditionReplaySummary } from './ConditionReplaySummary'

const fixture = (): PortfolioResult => ({
  config: { initial_capital: 100000 },
  strategies: [],
  per_strategy: [],
  ledger: [],
  equity_curve: [],
  summary: { net_return_pct: 0, max_drawdown_pct: 2, closed_trades: 10 },
  condition_replay: {
    version: 'research-condition-replay-v1',
    id: 'replay',
    condition: { strategy_id: 'Breakout', dimension: 'trend', regime: 'up', label: 'Rising' },
    parent_job_id: 'parent',
    parent_result_artifact: 'report-a',
    analysis_artifact: 'analysis-a',
    period: 'selection',
    baseline: { summary: { net_return_pct: -1, max_drawdown_pct: 3, closed_trades: 20 } },
    delta: { net_return_pct: 1, max_drawdown_pct: -1, closed_trades: -10 },
    counts: {
      allowed: 12,
      filtered: 8,
      unknown: 2,
      original_excluded: 1,
      pending: 0,
      unaffected: 5,
    },
    basis: ['Only entry information available at the time was used.'],
  },
})
describe('condition test summary', () => {
  it('compares real account results and keeps evidence details collapsed', async () => {
    const result = fixture()
    const original = JSON.stringify(result)
    const { container } = render(<ConditionReplaySummary result={result} />)
    const before = screen.getByRole('row', { name: /Unfiltered/ })
    expect(within(before).getByText('-1%')).toBeVisible()
    const after = screen.getByRole('row', { name: /With condition/ })
    expect(within(after).getByText('0%')).toBeVisible()
    expect(within(after).getByText('10')).toBeVisible()
    expect(screen.getByText(/not been selected or validated automatically/)).toBeVisible()
    expect(screen.getByText(/12 signals kept/)).not.toBeVisible()
    await userEvent.click(screen.getByText('Condition test details'))
    expect(
      screen.getByText(/12 signals kept · 8 skipped by condition · 2 unclassified/)
    ).toBeVisible()
    expect(JSON.stringify(result)).toBe(original)
    expect((await axe(container)).violations).toEqual([])
  })
  it('omits the section on ordinary reports and does not invent unavailable numbers', () => {
    const result = fixture()
    result.summary.net_return_pct = null
    const view = render(<ConditionReplaySummary result={result} />)
    expect(within(screen.getByRole('row', { name: /With condition/ })).getByText('—')).toBeVisible()
    view.rerender(<ConditionReplaySummary result={{ ...result, condition_replay: undefined }} />)
    expect(screen.queryByRole('region', { name: 'Condition test' })).not.toBeInTheDocument()
  })
})
