import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import type { AutomaticResearchFindings as Findings } from '@/api/portfolioResearch'
import { AutomaticResearchFindings } from './AutomaticResearchFindings'

function findings(): Findings {
  const summary = { net_return_pct: 2.8, max_drawdown_pct: 0.4, closed_trades: 26 }
  const baseline = { net_return_pct: 0.9, max_drawdown_pct: 1.2, closed_trades: 30 }
  return {
    version: 'automatic-trade-management-v1',
    status: 'supported',
    selected_config_id: 'c-2',
    selected_is_baseline: false,
    headline: 'The improvement held up on later dates',
    selection_basis:
      'Chosen using two earlier checks and higher costs; the final dates were checked afterward.',
    recipe: {
      version: 'automatic-trade-management-v1',
      periods: {
        search: { from: '2026-01-01', to: '2026-05-30' },
        check1: { from: '2026-06-01', to: '2026-06-30' },
        check2: { from: '2026-07-01', to: '2026-07-31' },
        final: { from: '2026-08-01', to: '2026-09-10' },
      },
    },
    baseline,
    checks: [
      {
        config_id: 'c-2',
        trial_number: 8,
        eligible: true,
        reasons: [],
        windows: [],
        stress: { summary, baseline_summary: baseline, score_delta: 1.3 },
        selection_score: 0.8,
      },
    ],
    final: { summary, baseline_summary: baseline, score_delta: 1.3, supports: true },
    counts: { proposals: 50, simulations: 64, finalists: 3 },
    unsupported_families: ['Indicators', 'Market regimes'],
  }
}

describe('automatic research findings', () => {
  it.each([
    ['automatic-trade-management-v1', 'Exits and holding periods'],
    ['automatic-hypotheses-v4', 'Smart research'],
    ['automatic-hypotheses-v5', 'Stock and market research'],
    ['automatic-future-v99', 'Automatic research'],
  ])('keeps the saved %s scope in the collapsed research details', async (version, label) => {
    const data = findings()
    data.version = version
    render(<AutomaticResearchFindings findings={data} />)
    const details = screen.getByText('Research details').closest('details') as HTMLElement
    expect(within(details).getByText(label)).not.toBeVisible()
    await userEvent.click(screen.getByText('Research details'))
    expect(within(details).getByText(label)).toBeVisible()
  })

  it('labels a sampled stability challenge without claiming all nearby settings were tested', async () => {
    const data = findings()
    data.version = 'automatic-trade-management-v2'
    data.checks[0].stability = {
      status: 'stable',
      tested: 8,
      passed: 6,
      pass_fraction: 0.75,
      available: 20,
      truncated: true,
    }
    render(<AutomaticResearchFindings findings={data} />)
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.getByText('Passed later-date, cost and nearby-setting checks')).toBeVisible()
    expect(screen.getByText('Nearby settings: 6 of 8 passed · sampled subset')).toBeVisible()
  })
  it('compares selected and original rules using the final period, with research details collapsed', async () => {
    render(<AutomaticResearchFindings findings={findings()} />)
    expect(screen.getByText('Automatic research')).toBeVisible()
    expect(screen.queryByText('Exploratory research')).not.toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: 'The improvement held up on later dates' })
    ).toBeVisible()
    const table = screen.getByRole('table', { name: 'Final period comparison' })
    expect(within(table).getByRole('row', { name: 'Original settings 0.9% 1.2% 30' })).toBeVisible()
    expect(within(table).getByRole('row', { name: 'Trial 9 2.8% 0.4% 26' })).toBeVisible()
    expect(screen.getByText(/Final check · 1 Aug/)).toHaveTextContent('10 Sept 2026')
    expect(screen.getByText(/50 proposals/)).not.toBeVisible()
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.getByText(/50 proposals · 64 simulations · 3 finalists/)).toBeVisible()
    expect(screen.getByText(/Not included in this run: Indicators, Market regimes/)).toBeVisible()
  })

  it('keeps a baseline fallback honest without presenting it as a new winning trial', () => {
    const data = findings()
    data.selected_is_baseline = true
    data.selected_config_id = 'baseline'
    data.status = 'not_supported'
    data.headline = 'No reliable improvement over your original settings'
    render(<AutomaticResearchFindings findings={data} />)
    const table = screen.getByRole('table', { name: 'Final period comparison' })
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    expect(within(table).queryByText('Trial 9')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: data.headline })).toBeVisible()
  })

  it('shows inconclusive evidence and missing values without inventing zero or confidence', () => {
    const data = findings()
    data.status = 'inconclusive'
    data.headline = 'More signals are needed for the final check'
    data.final!.summary = { net_return_pct: null, max_drawdown_pct: Number.NaN, closed_trades: 0 }
    render(<AutomaticResearchFindings findings={data} />)
    const row = screen.getByRole('row', { name: 'Trial 9 — — 0' })
    expect(row).toBeVisible()
    expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument()
  })

  it('does not show a final comparison when no final calculation exists', () => {
    const data = findings()
    data.final = null
    render(<AutomaticResearchFindings findings={data} />)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('has accessible semantics with expanded details', async () => {
    const { container } = render(<AutomaticResearchFindings findings={findings()} />)
    await userEvent.click(screen.getByText('Research details'))
    expect((await axe(container)).violations).toEqual([])
  })
})

it('does not claim later validation for an exploratory-only smart result', async () => {
  const data = findings()
  data.version = 'automatic-hypotheses-v2'
  data.recipe.validation_mode = 'exploratory_only'
  data.final = null
  data.checks = []
  data.status = 'inconclusive'
  render(<AutomaticResearchFindings findings={data} />)
  expect(screen.getByText('Best settings found on the available research dates.')).toBeVisible()
  expect(
    screen.queryByText('Checked on later dates and with higher trading costs.')
  ).not.toBeInTheDocument()
  expect(screen.queryByRole('table', { name: 'Final period comparison' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByText('Research details'))
  expect(screen.getByText(/No untouched later check/)).toBeVisible()
  expect(screen.queryByText('First check')).not.toBeInTheDocument()
  expect(screen.queryByText('Second check')).not.toBeInTheDocument()
})
