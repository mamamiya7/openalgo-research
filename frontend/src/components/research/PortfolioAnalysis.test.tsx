import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import type { AnalysisMetric, PortfolioResult } from '@/api/portfolioResearch'
import { researchDefaults } from '@/lib/researchDraft'
import { AnalysisCharts } from './AnalysisCharts'
import { AnalysisMetricTable } from './AnalysisMetricTable'
import {
  type AnalysisActions,
  PortfolioStudyAnalysis,
  PortfolioTearSheet,
} from './PortfolioAnalysis'

vi.mock('./ResearchPlot', () => ({
  default: ({ chart }: { chart: { id: string } }) => <div>Native chart {chart.id}</div>,
}))

const catalog: AnalysisMetric[] = [
  {
    key: 'account_return',
    label: 'Account return',
    group: 'Returns',
    format: 'percent',
    description: 'Marked account return.',
    source: 'Recorded account',
  },
  {
    key: 'vectorbt_sharpe',
    label: 'Sharpe ratio',
    group: 'Risk',
    format: 'number',
    description: 'Excess return divided by return volatility.',
    source: 'VectorBT Returns',
  },
  {
    key: 'vectorbt_beta',
    label: 'Beta',
    group: 'Benchmark',
    format: 'number',
    description: 'Sensitivity to benchmark returns.',
    source: 'VectorBT Returns',
  },
]
const result = (): PortfolioResult => ({
  config: { initial_capital: 100000 },
  strategies: [{ id: 'a', name: 'Breakout', allocation_pct: 100, config: { ...researchDefaults } }],
  summary: { net_return_pct: 0 },
  equity_curve: [],
  ledger: [],
  per_strategy: [],
  analysis: {
    version: 'research-analysis-v1',
    catalog,
    metrics: { account_return: 0, vectorbt_sharpe: null, vectorbt_beta: null },
    unavailable: {
      vectorbt_sharpe: 'Return volatility is zero.',
      vectorbt_beta: 'Select aligned benchmark prices.',
    },
    basis: ['Daily observations; 252 sessions per year.'],
    charts: [
      { id: 'equity', title: 'Equity', status: 'available', figure: { data: [], layout: {} } },
    ],
  },
})
const actions = (): AnalysisActions => ({
  busy: false,
  response: { status: 'missing' },
  onPrepare: vi.fn(),
  readOnly: false,
  exportUrl: '/analysis/export',
})

describe('full portfolio analysis', () => {
  it('shows exact values and undefined reasons, and filters native statistics by group or definition', async () => {
    const user = userEvent.setup()
    const value = result().analysis!
    const { container } = render(<AnalysisMetricTable catalog={catalog} analysis={value} />)
    expect(screen.getByText('0%')).toBeVisible()
    expect(screen.getAllByText('—')).toHaveLength(2)
    expect(screen.getByText('Return volatility is zero.')).not.toBeVisible()
    expect(screen.getByTitle('Return volatility is zero.')).toHaveTextContent('—')
    expect(screen.getByText('1 available · 2 unavailable')).toBeVisible()
    await user.click(screen.getByText('Sharpe ratio'))
    expect(screen.getByText('Return volatility is zero.')).toBeVisible()
    expect(screen.getByText('Risk · VectorBT Returns')).toBeVisible()
    expect((await axe(container)).violations).toEqual([])
    await user.selectOptions(screen.getByLabelText('Statistic group'), 'Benchmark')
    expect(screen.getByText('1 available · 2 unavailable · 1 shown')).toBeVisible()
    expect(screen.queryByText('Sharpe ratio')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Statistic group'), 'all')
    await user.type(screen.getByRole('textbox', { name: 'Find a statistic' }), 'excess')
    expect(screen.getByText('Sharpe ratio')).toBeVisible()
    expect(screen.queryByText('Beta')).not.toBeInTheDocument()
  })

  it('shows each native chart on demand and explains unavailable capabilities only when selected', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <AnalysisCharts
        charts={[
          ...result().analysis!.charts,
          {
            id: 'pareto',
            title: 'Pareto front',
            status: 'unavailable',
            reason: 'This study records one objective.',
          },
        ]}
      />
    )
    expect(await screen.findByText('Native chart equity')).toBeVisible()
    expect(screen.queryByText('This study records one objective.')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Analysis chart' }), 'pareto')
    expect(screen.getByText('This study records one objective.')).toBeVisible()
    expect(screen.queryByText('Native chart equity')).not.toBeInTheDocument()
    expect((await axe(container)).violations).toEqual([])
  })

  it('prepares old evidence explicitly, disables repeat work while queued, and respects read-only results', async () => {
    const props = actions()
    const old = { ...result(), analysis: undefined }
    const { rerender } = render(<PortfolioTearSheet result={old} {...props} />)
    expect(props.onPrepare).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare analysis' }))
    expect(props.onPrepare).toHaveBeenCalledExactlyOnceWith()
    rerender(<PortfolioTearSheet result={old} {...props} busy response={{ status: 'running' }} />)
    expect(screen.getByText('Preparing analysis…')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Prepare analysis' })).not.toBeInTheDocument()
    rerender(<PortfolioTearSheet result={old} {...props} readOnly />)
    expect(screen.queryByRole('button', { name: 'Prepare analysis' })).not.toBeInTheDocument()
    expect(screen.getByText('Detailed analysis has not been saved for this result.')).toBeVisible()
  })

  it('sends the chosen study axes only on Update charts and preserves native plot requirements', async () => {
    const user = userEvent.setup()
    const value = result()
    value.experiment = {
      kind: 'portfolio_optimize',
      rows: [],
      recommendation_id: 'a',
      selected_strategies: [],
      optimizer: { sampler: 'tpe', objective_definition: 'return', version: '5.0.0' },
      specification: { sampler: 'tpe', trials: 10, objective: 'return', seed: 0 },
      counts: {},
      search_space: {
        axes: {
          'a.target_pct': { min: 1, max: 3, step: 1 },
          'a.stop_pct': { min: 1, max: 3, step: 1 },
          'a.hold_sessions': { min: 1, max: 3, step: 1 },
        },
      },
      study_analysis: {
        version: 'v1',
        basis: ['Optuna 5.0.0; saved trial observations.'],
        charts: value.analysis!.charts,
      },
    }
    const props = actions()
    render(<PortfolioStudyAnalysis result={value} {...props} />)
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Study parameter 1' }),
      'a.hold_sessions'
    )
    expect(props.onPrepare).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Update charts' }))
    expect(props.onPrepare).toHaveBeenCalledExactlyOnceWith(['a.hold_sessions', 'a.stop_pct'])
    expect(screen.getByRole('option', { name: 'Stop loss', selected: true })).toBeInTheDocument()
  })

  it('keeps large native record sets bounded and searchable', async () => {
    const user = userEvent.setup()
    const value = result()
    value.engine_records = {
      fills: Array.from({ length: 101 }, (_, index) => ({
        id: index,
        symbol: `SYMBOL${index}`,
        commission: index === 0 ? 0 : null,
      })),
    }
    render(<PortfolioTearSheet result={value} {...actions()} />)
    await user.click(screen.getByText('Engine records'))
    const table = screen.getByRole('table', { name: 'Native engine fills' })
    expect(within(table).getAllByRole('row')).toHaveLength(26)
    await user.click(screen.getByRole('button', { name: 'Next records' }))
    expect(screen.getByText('26–50 of 101')).toBeVisible()
    await user.type(screen.getByRole('textbox', { name: 'Find an engine record' }), 'SYMBOL100')
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    expect(screen.getByText('1–1 of 1')).toBeVisible()
  })

  it('shows the price symbol control only on the price chart and requests saved prices explicitly', async () => {
    const user = userEvent.setup()
    const value = result()
    value.analysis!.price_symbols = ['ABC', 'XYZ']
    value.analysis!.price_symbol = 'ABC'
    value.analysis!.charts.push({
      id: 'bars-with-fills',
      title: 'Prices and fills',
      status: 'available',
      figure: { data: [], layout: {} },
    })
    const props = actions()
    render(<PortfolioTearSheet result={value} {...props} />)
    expect(screen.queryByRole('combobox', { name: 'Price chart symbol' })).not.toBeInTheDocument()
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Analysis chart' }),
      'bars-with-fills'
    )
    await user.selectOptions(screen.getByRole('combobox', { name: 'Price chart symbol' }), 'XYZ')
    expect(props.onPrepare).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Show prices' }))
    expect(props.onPrepare).toHaveBeenCalledExactlyOnceWith(undefined, 'XYZ')
  })

  it('keeps a one-year monthly heatmap compact', () => {
    render(
      <AnalysisCharts
        charts={[
          {
            id: 'monthly-returns',
            title: 'Monthly returns',
            status: 'available',
            figure: { data: [{ type: 'heatmap', y: ['2026'], x: ['01'], z: [[1]] }], layout: {} },
          },
        ]}
      />
    )
    expect(screen.getByRole('figure', { name: 'Monthly returns' })).toHaveStyle({ height: '220px' })
  })
})
