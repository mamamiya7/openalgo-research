import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { type ComponentProps, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PortfolioResult, PortfolioSettings } from '@/api/portfolioResearch'
import { researchDefaults } from '@/lib/researchDraft'
import { useAuthStore } from '@/stores/authStore'
import { PortfolioTrials } from './PortfolioTrials'

function experiment(count = 2): NonNullable<PortfolioResult['experiment']> {
  const rows = Array.from({ length: count }, (_, index) => ({
    config_id: `trial-${index}`,
    trial_number: index,
    score: 8 - index,
    stage: 'tpe',
    strategies: [
      {
        id: 'strategy',
        name: 'Breakout',
        allocation_pct: 100,
        config: { ...researchDefaults, target_pct: index + 1 },
      },
    ],
    summary: {
      initial_capital: 100000,
      final_equity: 110000,
      net_pnl: 10000,
      net_return_pct: 10,
      max_drawdown_pct: 2,
      win_rate_pct: 60,
      profit_factor: 1.5,
      realized_equity: 108000,
      accepted_trades: 12,
      closed_trades: 10,
      pending_trades: 2,
      unfunded_pending: 0,
      skipped_trades: 0,
      excluded_signals: 1,
      sample_adequacy: 'Insufficient sample',
    },
  }))
  return {
    kind: 'portfolio_optimize',
    rows,
    recommendation_id: 'trial-0',
    selected_strategies: rows[0].strategies,
    optimizer: {
      sampler: 'TPESampler',
      objective_definition: 'return minus drawdown',
      version: '5',
    },
    specification: { sampler: 'tpe', trials: count, objective: 'balanced', seed: 0 },
    counts: { evaluated_this_pass: count, rejected_allocations: 0, reused_trials: 0 },
  }
}
const renderSettings = (strategy: PortfolioSettings) => (
  <p>
    {strategy.name}: target {strategy.config.target_pct}%
  </p>
)
const owner = (username: string) =>
  useAuthStore.setState({ user: { username, broker: null, isLoggedIn: true, loginTime: null } })
const key = 'research-trial-columns:v1:alice'
const headings = () =>
  within(screen.getByRole('table', { name: 'Optimization trials' }))
    .getAllByRole('columnheader')
    .map((cell) => cell.textContent)
function mount(value = experiment(), readOnly = false) {
  const rerun = vi.fn()
  const props = { experiment: value, onRerun: rerun, rerunning: false, readOnly, renderSettings }
  return { ...render(<Harness {...props} />), rerun, props }
}

function Harness(props: Omit<ComponentProps<typeof PortfolioTrials>, 'page' | 'onPageChange'>) {
  const [page, setPage] = useState(0)
  return <PortfolioTrials {...props} page={page} onPageChange={setPage} />
}

beforeEach(() => {
  localStorage.clear()
  owner('alice')
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('optimization trial comparison', () => {
  it('opens the exact paginated trial in a dialog and restores focus without losing table position', async () => {
    const user = userEvent.setup()
    const { rerun } = mount(experiment(125))
    expect(screen.getAllByRole('button', { name: 'View settings' })).toHaveLength(25)
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('26–50 of 125')).toBeVisible()
    const region = screen.getByRole('region', { name: 'Trial comparison' })
    region.scrollTop = 180
    const trigger = screen.getAllByRole('button', { name: 'View settings' })[7]
    await user.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'Trial 33 settings' })
    expect(within(dialog).getByRole('heading', { name: 'Trial 33 settings' })).toHaveFocus()
    expect(within(dialog).getByText('Breakout: target 33%')).toBeVisible()
    expect((await axe(dialog)).violations).toEqual([])
    await user.keyboard('{Escape}')
    await waitFor(() => expect(trigger).toHaveFocus())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('26–50 of 125')).toBeVisible()
    expect(region.scrollTop).toBe(180)
    expect(rerun).not.toHaveBeenCalled()
  })

  it('shows saved win rate/profit factor and persists chosen columns without rerunning', async () => {
    const user = userEvent.setup()
    const { unmount, rerun, props } = mount()
    expect(headings()).toEqual([
      'Trial',
      'Return',
      'Max drawdown',
      'Win rate',
      'Profit factor',
      'Closed trades',
      'Actions',
    ])
    const trigger = screen.getByRole('button', { name: 'Columns' })
    await user.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'Table columns' })
    expect(within(dialog).getAllByRole('checkbox')).toHaveLength(16)
    expect((await axe(dialog)).violations).toEqual([])
    await user.click(within(dialog).getByRole('checkbox', { name: 'Return', exact: true }))
    await user.click(within(dialog).getByRole('checkbox', { name: 'Net P&L', exact: true }))
    await user.click(within(dialog).getByRole('checkbox', { name: 'Objective score', exact: true }))
    await user.click(within(dialog).getByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(trigger).toHaveFocus())
    expect(headings()).not.toContain('Return')
    expect(headings()).toContain('Net P&L')
    expect(headings()).toContain('Objective score')
    expect(screen.getAllByText('₹10,000.00')).toHaveLength(2)
    expect(JSON.parse(localStorage.getItem(key)!)).toContain('objective_score')
    unmount()
    render(<Harness {...props} />)
    expect(headings()).toContain('Net P&L')
    expect(headings()).not.toContain('Return')
    expect(rerun).not.toHaveBeenCalled()
  })

  it('discards canceled selections, resets defaults on Apply, and keeps at least one metric', async () => {
    localStorage.setItem(key, JSON.stringify(['net_pnl']))
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: 'Columns' }))
    expect(screen.getByRole('checkbox', { name: 'Net P&L', exact: true })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Win rate' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(headings()).toEqual(['Trial', 'Net P&L', 'Actions'])
    await user.click(screen.getByRole('button', { name: 'Columns' }))
    await user.click(screen.getByRole('button', { name: 'Reset to default' }))
    await user.click(screen.getByRole('button', { name: 'Apply' }))
    expect(headings()).toContain('Win rate')
    expect(headings()).not.toContain('Net P&L')
  })

  it('preserves zero and missing values without substituting winner metrics or fabricated statistics', async () => {
    const value = experiment(3)
    value.rows[0].summary.win_rate_pct = 0
    value.rows[0].summary.profit_factor = 0
    value.rows[1].summary.win_rate_pct = null
    value.rows[1].summary.profit_factor = null
    delete value.rows[2].summary.win_rate_pct
    value.rows[2].summary.profit_factor = Number.POSITIVE_INFINITY
    mount(value)
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('0%')).toBeVisible()
    expect(within(rows[0]).getByText('0', { selector: 'td' })).toBeVisible()
    expect(within(rows[1]).getAllByText('—')).toHaveLength(2)
    expect(within(rows[2]).getAllByText('—')).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: 'Columns' }))
    expect(
      screen.queryByRole('checkbox', { name: /Sharpe|Sortino|CAGR|Available cash/i })
    ).not.toBeInTheDocument()
  })

  it('handles older summaries and stale preferences using only fields present in the study', async () => {
    localStorage.setItem(key, JSON.stringify(['net_pnl', 'old_unknown_metric']))
    const value = experiment(1)
    value.rows[0].summary = { net_return_pct: 1, max_drawdown_pct: 0.5, closed_trades: 5 }
    mount(value)
    expect(headings()).toEqual(['Trial', 'Return', 'Max drawdown', 'Closed trades', 'Actions'])
    await userEvent.click(screen.getByRole('button', { name: 'Columns' }))
    expect(screen.queryByRole('checkbox', { name: 'Win rate' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Objective score' })).toBeVisible()
  })

  it('separates column preferences when the signed-in account changes', () => {
    localStorage.setItem(key, JSON.stringify(['net_pnl']))
    localStorage.setItem('research-trial-columns:v1:bob', JSON.stringify(['closed_trades']))
    mount()
    expect(headings()).toEqual(['Trial', 'Net P&L', 'Actions'])
    act(() => owner('bob'))
    expect(headings()).toEqual(['Trial', 'Closed trades', 'Actions'])
  })

  it('remains usable when browser storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Storage blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage blocked')
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Columns' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Net P&L', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(headings()).toContain('Net P&L')
  })

  it('backtests the viewed candidate only on request and respects read-only reports', async () => {
    const value = experiment(2)
    const { rerun, rerender, props } = mount(value)
    await userEvent.click(screen.getAllByRole('button', { name: 'View settings' })[1])
    await userEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Backtest this' })
    )
    expect(rerun).toHaveBeenCalledExactlyOnceWith('trial-1')
    rerender(<Harness {...props} readOnly />)
    await userEvent.click(screen.getAllByRole('button', { name: 'View settings' })[0])
    expect(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Backtest this' })
    ).toBeDisabled()
  })

  it('selects and remembers native trial statistics without borrowing values from another trial', async () => {
    const user = userEvent.setup()
    const value = experiment(2)
    value.analysis_catalog = [
      {
        key: 'vectorbt_sharpe',
        label: 'Sharpe ratio',
        group: 'Risk',
        format: 'number',
        description: 'Native account return statistics.',
        source: 'VectorBT',
      },
    ]
    value.rows[0].analysis = { version: 'v1', metrics: { vectorbt_sharpe: 1.25 }, unavailable: {} }
    value.rows[1].analysis = {
      version: 'v1',
      metrics: { vectorbt_sharpe: null },
      unavailable: { vectorbt_sharpe: 'No return variance.' },
    }
    const { unmount, props, rerun } = mount(value)
    await user.click(screen.getByRole('button', { name: 'Columns' }))
    await user.click(screen.getByRole('checkbox', { name: 'Sharpe ratio' }))
    await user.click(screen.getByRole('button', { name: 'Apply' }))
    expect(headings()).toContain('Sharpe ratio')
    expect(screen.getByText('1.25')).toBeVisible()
    expect(screen.getByTitle('No return variance.')).toHaveTextContent('—')
    unmount()
    render(<Harness {...props} />)
    expect(headings()).toContain('Sharpe ratio')
    await user.click(screen.getAllByRole('button', { name: 'View settings' })[1])
    await user.click(screen.getByRole('tab', { name: 'Statistics' }))
    expect(within(screen.getByRole('dialog')).getByTitle('No return variance.')).toHaveTextContent(
      '—'
    )
    await user.click(within(screen.getByRole('dialog')).getByText('Sharpe ratio'))
    expect(within(screen.getByRole('dialog')).getByText('No return variance.')).toBeVisible()
    expect(within(screen.getByRole('dialog')).queryByText('1.25')).not.toBeInTheDocument()
    expect(rerun).not.toHaveBeenCalled()
  })
})
