import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { MarketSuitabilityFindings, MarketSuitabilityPeriod } from '@/api/portfolioResearch'
import { MarketSuitability } from './MarketSuitability'

function period(): MarketSuitabilityPeriod {
  return {
    dates: { from: '2026-01-05', to: '2026-06-30' },
    coverage: {
      total_sessions: 120,
      classified_sessions: 110,
      closed_trades: 80,
      classified_trades: 70,
      unclassified_trades: 10,
    },
    cohorts: [
      {
        strategy_id: 'scanner',
        strategy_name: 'Scanner',
        dimension: 'trend',
        regime: 'up',
        label: 'Rising trend',
        closed_trades: 40,
        entry_sessions: 22,
        average_net_return_pct: 1.25,
        win_rate_pct: 55,
        net_pnl: 500,
        evidence: 'descriptive',
      },
    ],
    finding: {
      status: 'observed',
      text: 'Scanner showed stronger average trade returns in rising trend on this saved sample.',
      next_step: 'A separate filtered backtest is needed.',
    },
  }
}

function suitability(): MarketSuitabilityFindings {
  return {
    version: 'research-market-suitability-v1',
    status: 'partial',
    development: period(),
    limitations: ['Trade cohorts are not filtered portfolio returns.'],
  }
}

describe('automatic market suitability', () => {
  it('leaves older reports and unknown versions without an empty panel', () => {
    const { container, rerender } = render(<MarketSuitability />)
    expect(container).toBeEmptyDOMElement()
    rerender(
      <MarketSuitability
        suitability={
          { ...suitability(), version: 'future-v2' } as unknown as MarketSuitabilityFindings
        }
      />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the development association and actual coverage with the breakdown collapsed', () => {
    render(<MarketSuitability suitability={suitability()} />)
    const region = screen.getByRole('region', { name: 'Market suitability' })
    expect(within(region).getByText(period().finding.text)).toBeVisible()
    expect(within(region).getByText(/70 of 80 closed trades classified/)).toBeVisible()
    expect(within(region).getByText(/110 of 120 sessions/)).toBeVisible()
    expect(within(region).getByText(/10 trades unclassified/)).toBeVisible()
    expect(within(region).getByText('Historical association, not a trading filter.')).toBeVisible()
    expect(region.querySelector('details')).not.toHaveAttribute('open')
    expect(within(region).getByRole('table')).not.toBeVisible()
    expect(within(region).queryByRole('button')).not.toBeInTheDocument()
  })

  it('opens cohort arithmetic and keeps final outcomes separate from the headline', async () => {
    const data = suitability()
    data.final = period()
    data.final.dates = { from: '2026-07-01', to: '2026-09-30' }
    data.final.finding.text = 'Final-only stronger high volatility finding'
    data.final.cohorts[0] = {
      ...data.final.cohorts[0],
      label: 'High volatility',
      average_net_return_pct: -3.5,
    }
    render(<MarketSuitability suitability={data} />)
    expect(screen.queryByText(data.final.finding.text)).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('View condition breakdown'))
    const development = screen.getByRole('region', { name: 'Development market conditions' })
    const final = screen.getByRole('region', { name: 'Final market conditions' })
    expect(within(development).getByRole('row', { name: 'Rising trend 40 22 1.25%' })).toBeVisible()
    expect(within(final).getByRole('row', { name: 'High volatility 40 22 -3.5%' })).toBeVisible()
    expect(
      within(final).getByRole('heading', { name: /Final check · kept separate/ })
    ).toBeVisible()
    expect(
      screen.getByText('Final-period outcomes do not choose settings or the development finding.')
    ).toBeVisible()
    expect(screen.queryByText(data.final.finding.text)).not.toBeInTheDocument()
  })

  it('labels sparse and unknown cohorts without a confident filter suggestion', async () => {
    const data = suitability()
    data.development!.finding = {
      status: 'insufficient',
      text: 'Recommend this filter',
      next_step: 'Do something',
    }
    data.development!.cohorts[0] = {
      ...data.development!.cohorts[0],
      regime: 'unknown',
      label: 'Unclassified',
      average_net_return_pct: null,
      evidence: 'limited',
    }
    render(<MarketSuitability suitability={data} />)
    expect(screen.getByText('Not enough evidence to distinguish market conditions.')).toBeVisible()
    expect(screen.queryByText('Recommend this filter')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('View condition breakdown'))
    expect(screen.getByRole('row', { name: 'Unclassified · Limited sample 40 22 —' })).toBeVisible()
  })

  it('explains unavailable saved context without inventing coverage or a result', () => {
    render(
      <MarketSuitability
        suitability={{
          version: 'research-market-suitability-v1',
          status: 'unavailable',
          reason: 'This saved run has no frozen market context.',
          limitations: [],
        }}
      />
    )
    expect(screen.getByText('This saved run has no frozen market context.')).toBeVisible()
    expect(screen.queryByText(/closed trades classified/)).not.toBeInTheDocument()
    expect(screen.queryByText('View condition breakdown')).not.toBeInTheDocument()
  })

  it('identifies each strategy and an empty final sample in the same collapsed breakdown', async () => {
    const data = suitability()
    data.development!.cohorts.push({
      ...data.development!.cohorts[0],
      strategy_id: 'second',
      strategy_name: 'Second scanner',
    })
    data.final = { ...period(), cohorts: [] }
    render(<MarketSuitability suitability={data} />)
    await userEvent.click(screen.getByText('View condition breakdown'))
    const development = screen.getByRole('region', { name: 'Development market conditions' })
    expect(
      within(development).getByRole('row', { name: 'Scanner · Rising trend 40 22 1.25%' })
    ).toBeVisible()
    expect(
      within(development).getByRole('row', { name: 'Second scanner · Rising trend 40 22 1.25%' })
    ).toBeVisible()
    expect(
      within(screen.getByRole('region', { name: 'Final market conditions' })).getByText(
        'No closed trades to compare.'
      )
    ).toBeVisible()
  })

  it('keeps the disclosure keyboard reachable and retains focus when toggling', async () => {
    const user = userEvent.setup()
    const { container } = render(<MarketSuitability suitability={suitability()} />)
    await user.tab()
    expect(screen.getByText('View condition breakdown')).toHaveFocus()
    await user.click(screen.getByText('View condition breakdown'))
    expect(container.querySelector('details')).toHaveAttribute('open')
    expect(screen.getByRole('table')).toBeVisible()
    await user.click(screen.getByText('View condition breakdown'))
    expect(container.querySelector('details')).not.toHaveAttribute('open')
    expect(screen.getByText('View condition breakdown')).toHaveFocus()
  })
})
