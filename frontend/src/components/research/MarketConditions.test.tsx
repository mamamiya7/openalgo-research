import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import type { MarketConditionsAnalysis, PortfolioAnalysis } from '@/api/portfolioResearch'
import { MarketConditions } from './MarketConditions'
import type { AnalysisActions } from './PortfolioAnalysis'

const actions = (): AnalysisActions => ({
  busy: false,
  response: { status: 'missing' },
  onPrepare: vi.fn(),
  readOnly: false,
  exportUrl: '/analysis/export',
})
const conditions = (): MarketConditionsAnalysis => ({
  version: 'research-market-conditions-v1',
  status: 'partial',
  descriptor: { symbol: 'NIFTY', exchange: 'NSE_INDEX', interval: 'D', role: 'benchmark' },
  dates: { from: '2026-01-05', to: '2026-01-08' },
  coverage: {
    total_sessions: 4,
    classified_sessions: 3,
    closed_trades: 30,
    classified_trades: 28,
    unclassified_trades: 2,
  },
  timeline: [
    {
      date: '2026-01-05',
      trend: 'up',
      volatility: 'low',
      stress: 'normal',
      observed_through: '2026-01-02',
    },
    {
      date: '2026-01-06',
      trend: 'up',
      volatility: 'high',
      stress: 'elevated',
      observed_through: '2026-01-05',
    },
    {
      date: '2026-01-07',
      trend: 'range',
      volatility: 'normal',
      stress: 'normal',
      observed_through: '2026-01-06',
    },
    {
      date: '2026-01-08',
      trend: 'unknown',
      volatility: 'unknown',
      stress: 'unknown',
      observed_through: null,
    },
  ],
  cohorts: [
    {
      strategy_id: 'a',
      strategy_name: 'Breakout',
      dimension: 'trend',
      regime: 'up',
      label: 'Rising',
      closed_trades: 25,
      entry_sessions: 8,
      average_net_return_pct: 1.5,
      win_rate_pct: 60,
      net_pnl: 350,
      evidence: 'descriptive',
    },
    {
      strategy_id: 'a',
      strategy_name: 'Breakout',
      dimension: 'trend',
      regime: 'range',
      label: 'Sideways',
      closed_trades: 3,
      entry_sessions: 1,
      average_net_return_pct: null,
      win_rate_pct: 0,
      net_pnl: 0,
      evidence: 'limited',
    },
    {
      strategy_id: 'a',
      strategy_name: 'Breakout',
      dimension: 'volatility',
      regime: 'high',
      label: 'High volatility',
      closed_trades: 12,
      entry_sessions: 5,
      average_net_return_pct: -0.4,
      win_rate_pct: 40,
      net_pnl: -100,
      evidence: 'limited',
    },
    {
      strategy_id: 'b',
      strategy_name: 'Reversal',
      dimension: 'trend',
      regime: 'down',
      label: 'Falling',
      closed_trades: 20,
      entry_sessions: 6,
      average_net_return_pct: 0,
      win_rate_pct: 50,
      net_pnl: 0,
      evidence: 'descriptive',
    },
  ],
  finding: {
    status: 'insufficient',
    text: 'Not enough evidence to distinguish conditions.',
    next_step: 'More separate entry dates are needed.',
  },
  basis: ['Each entry uses only previously available market closes.'],
  recipe: { lookback: 63 },
})
const analysis = (value = conditions()): PortfolioAnalysis => ({
  version: 'v1',
  metrics: {},
  unavailable: {},
  catalog: [],
  charts: [],
  basis: [],
  market_conditions: value,
})

describe('saved market conditions', () => {
  it('tests only a supported cohort and preserves the exact chosen strategy and condition', async () => {
    const replay = {
      busy: false,
      pending: null,
      error: null,
      test: vi.fn().mockResolvedValue(undefined),
    }
    render(<MarketConditions {...actions()} analysis={analysis()} conditionReplay={replay} />)
    expect(replay.test).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Test sideways condition' })).toBeEnabled()
    expect(screen.getByRole('row', { name: /Sideways/ })).toHaveTextContent('Small sample')
    await userEvent.click(screen.getByRole('button', { name: 'Test rising condition' }))
    expect(replay.test).toHaveBeenCalledExactlyOnceWith({
      strategy_id: 'a',
      dimension: 'trend',
      regime: 'up',
    })
    await userEvent.selectOptions(screen.getByLabelText('Strategy'), 'b')
    await userEvent.click(screen.getByRole('button', { name: 'Test falling condition' }))
    expect(replay.test).toHaveBeenLastCalledWith({
      strategy_id: 'b',
      dimension: 'trend',
      regime: 'down',
    })
  })

  it('shows pending and retry errors beside conditions, with actions disabled while starting', () => {
    const replay = {
      busy: true,
      pending: JSON.stringify(['a', 'trend', 'up']),
      error: null as string | null,
      test: vi.fn(),
    }
    const view = render(
      <MarketConditions {...actions()} analysis={analysis()} conditionReplay={replay} />
    )
    expect(screen.getByRole('button', { name: 'Test rising condition' })).toBeDisabled()
    expect(screen.getByText('Starting…')).toBeVisible()
    view.rerender(
      <MarketConditions
        {...actions()}
        analysis={analysis()}
        conditionReplay={{
          ...replay,
          busy: false,
          pending: null,
          error: 'Request interrupted. Try again.',
        }}
      />
    )
    expect(screen.getByText('Request interrupted. Try again.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Test rising condition' })).toBeEnabled()
  })

  it.each([
    { readOnly: true },
    { freezeAnalysis: true },
  ])('hides condition tests for retained evidence: %j', (guard) => {
    render(
      <MarketConditions
        {...actions()}
        {...guard}
        analysis={analysis()}
        conditionReplay={{ busy: false, pending: null, error: null, test: vi.fn() }}
      />
    )
    expect(screen.queryByRole('button', { name: /Test .* condition/ })).not.toBeInTheDocument()
  })

  it('waits for an explicit action and sends only the conditions flag', async () => {
    const props = actions()
    render(<MarketConditions {...props} />)
    expect(props.onPrepare).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Analyze market conditions' }))
    expect(props.onPrepare).toHaveBeenCalledExactlyOnceWith(undefined, undefined, undefined, true)
  })

  it('shows historical trend and volatility separately with honest counts and accessible cohort controls', async () => {
    const value = conditions()
    const before = JSON.stringify(value)
    const { container } = render(<MarketConditions {...actions()} analysis={analysis(value)} />)
    const trend = screen.getByRole('list', { name: 'Historical trend, earliest to latest' })
    expect(within(trend).getAllByRole('listitem')).toHaveLength(3)
    expect(within(trend).getAllByRole('listitem')[0]).toHaveAccessibleName(
      'Rising · 5–6 Jan 2026 · 2 sessions'
    )
    expect(
      screen.getByRole('list', { name: 'Historical volatility, earliest to latest' })
    ).toBeVisible()
    expect(
      screen.getByText(
        /3 of 4 sessions classified · 28 of 30 closed trades classified · 2 unclassified/
      )
    ).toBeVisible()
    const table = screen.getByRole('table')
    expect(within(table).getByRole('columnheader', { name: 'Avg. net trade return' })).toBeVisible()
    expect(within(table).getByRole('row', { name: /Rising/ })).toHaveTextContent('25')
    expect(within(table).getByRole('row', { name: /Sideways/ })).toHaveTextContent('Small sample')
    expect(within(table).getByRole('row', { name: /Sideways/ })).toHaveTextContent('—')
    expect(within(table).getByRole('row', { name: /Sideways/ })).toHaveTextContent('0%')
    await userEvent.click(screen.getByRole('button', { name: 'Volatility', exact: true }))
    expect(within(table).getByRole('row', { name: /High volatility/ })).toHaveTextContent('-0.4%')
    await userEvent.click(screen.getByRole('button', { name: 'Trend', exact: true }))
    await userEvent.selectOptions(screen.getByLabelText('Strategy'), 'b')
    expect(within(table).getByRole('row', { name: /Falling/ })).toHaveTextContent('0%')
    expect(within(table).queryByRole('row', { name: /Rising/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Test this condition/ })).not.toBeInTheDocument()
    expect(JSON.stringify(value)).toBe(before)
    expect((await axe(container)).violations).toEqual([])
  })

  it('retains saved findings while preparing and requests the exact pending retry after failure', async () => {
    const props = actions()
    const { rerender } = render(
      <MarketConditions
        {...props}
        analysis={analysis()}
        busy
        response={{ status: 'running', requested_market_conditions: true }}
      />
    )
    expect(screen.getByText('Preparing market conditions…')).toBeVisible()
    expect(screen.getByText('Not enough evidence to distinguish conditions.')).toBeVisible()
    rerender(
      <MarketConditions
        {...props}
        analysis={analysis()}
        response={{
          status: 'failed',
          requested_market_conditions: true,
          error: 'Broker unavailable. Saved report retained.',
        }}
      />
    )
    expect(screen.getByText('Broker unavailable. Saved report retained.')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry market conditions' }))
    expect(props.onPrepare).toHaveBeenCalledExactlyOnceWith()
  })

  it('uses combined coverage while keeping known volatility and unclassified trend visible', async () => {
    const value = conditions()
    value.timeline[3] = { ...value.timeline[3], volatility: 'normal', stress: 'normal' }
    value.cohorts.push({
      ...value.cohorts[0],
      regime: 'unknown',
      label: 'Unclassified',
      closed_trades: 2,
      entry_sessions: 2,
      evidence: 'limited',
    })
    render(<MarketConditions {...actions()} analysis={analysis(value)} />)
    expect(
      screen.getByText(/3 of 4 sessions classified · 28 of 30 closed trades classified/)
    ).toBeVisible()
    const unclassified = screen.getByRole('row', { name: /Unclassified/ })
    expect(unclassified).toHaveTextContent('2')
    expect(unclassified).not.toHaveTextContent('Small sample')
    expect(
      within(
        screen.getByRole('list', { name: 'Historical volatility, earliest to latest' })
      ).getAllByRole('listitem')
    ).toHaveLength(3)
    await userEvent.click(screen.getByRole('button', { name: 'Volatility', exact: true }))
    expect(screen.getByRole('row', { name: /High volatility/ })).toBeVisible()
    expect(
      screen.getByText(/3 of 4 sessions classified · 28 of 30 closed trades classified/)
    ).toBeVisible()
  })

  it.each([
    { readOnly: true },
    { freezeAnalysis: true },
  ])('keeps retained evidence readable without preparation: %j', (guard) => {
    const props = actions()
    const { rerender } = render(<MarketConditions {...props} {...guard} />)
    expect(screen.queryByRole('region', { name: 'Market conditions' })).not.toBeInTheDocument()
    rerender(<MarketConditions {...props} {...guard} analysis={analysis()} />)
    expect(screen.getByText('Not enough evidence to distinguish conditions.')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: /Analyze market conditions|Retry market conditions/ })
    ).not.toBeInTheDocument()
    expect(props.onPrepare).not.toHaveBeenCalled()
  })

  it('shows insufficient data without a timeline or invented cohort values', () => {
    const value = conditions()
    value.status = 'unavailable'
    value.finding.text = 'No historical conditions could be classified.'
    render(<MarketConditions {...actions()} analysis={analysis(value)} />)
    expect(screen.getByText(value.finding.text)).toBeVisible()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Retry market conditions' })
    ).not.toBeInTheDocument()
  })

  it('does not add another preparation action when complete and keeps measurement details collapsed', async () => {
    const value = conditions()
    value.status = 'available'
    render(<MarketConditions {...actions()} analysis={analysis(value)} />)
    expect(
      screen.queryByRole('button', { name: /Analyze market conditions|Retry market conditions/ })
    ).not.toBeInTheDocument()
    expect(screen.getByText(value.basis[0])).not.toBeVisible()
    await userEvent.click(screen.getByText('How conditions were measured'))
    expect(screen.getByText(value.basis[0])).toBeVisible()
  })
})
