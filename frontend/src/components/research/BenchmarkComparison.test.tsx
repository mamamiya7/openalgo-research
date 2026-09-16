import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import type { BenchmarkAnalysis, PortfolioAnalysis } from '@/api/portfolioResearch'
import { BenchmarkComparison, niftyBenchmark } from './BenchmarkComparison'
import type { AnalysisActions } from './PortfolioAnalysis'

vi.mock('./AnalysisCharts', () => ({ AnalysisFigure: () => <div>Aligned benchmark chart</div> }))
const actions = (): AnalysisActions => ({
  busy: false,
  response: { status: 'missing' },
  onPrepare: vi.fn(),
  readOnly: false,
  exportUrl: '/analysis/export',
})
const benchmark = (): BenchmarkAnalysis => ({
  version: 'v1',
  status: 'available',
  descriptor: niftyBenchmark,
  dates: { from: '2026-01-01', to: '2026-08-31' },
  observations: 145,
  omitted_sessions: 0,
  metrics: {
    portfolio_return_pct: 6,
    benchmark_return_pct: 4,
    excess_return_pct: 2,
    beta: 0.8,
    alpha_pct: 1.1,
    correlation: 0.7,
    tracking_error_pct: 3,
    information_ratio: 0.5,
  },
  basis: ['Daily aligned closes; no forward filling.'],
})
const analysis = (value = benchmark()): PortfolioAnalysis => ({
  version: 'v1',
  metrics: {},
  unavailable: {},
  catalog: [],
  basis: [],
  benchmark: value,
  charts: [
    {
      id: 'benchmark-comparison',
      title: 'Benchmark comparison',
      status: 'available',
      figure: { data: [], layout: {} },
    },
  ],
})

describe('explicit market benchmark', () => {
  it('waits for a click and submits only the named daily benchmark', async () => {
    const props = actions()
    render(<BenchmarkComparison {...props} />)
    expect(props.onPrepare).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Add Nifty 50 benchmark' }))
    expect(props.onPrepare).toHaveBeenCalledExactlyOnceWith(undefined, undefined, niftyBenchmark)
  })

  it('keeps comparison results readable while preparing or retrying a failed update', async () => {
    const props = actions()
    const saved = analysis()
    const { rerender } = render(
      <BenchmarkComparison
        {...props}
        analysis={saved}
        busy
        response={{ status: 'running', requested_benchmark: niftyBenchmark }}
      />
    )
    expect(screen.getByText('Preparing benchmark…')).toBeVisible()
    expect(screen.getByText('6%')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Retry benchmark' })).not.toBeInTheDocument()
    rerender(
      <BenchmarkComparison
        {...props}
        analysis={saved}
        response={{
          status: 'failed',
          requested_benchmark: niftyBenchmark,
          error: 'Broker unavailable; report unchanged.',
        }}
      />
    )
    expect(screen.getByText('6%')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry benchmark' }))
    expect(props.onPrepare).toHaveBeenCalledWith()
  })

  it('labels partial coverage and keeps benchmark metrics separate from full-period statistics', async () => {
    const saved = benchmark()
    saved.status = 'partial'
    saved.omitted_sessions = 4
    saved.metrics.beta = null
    const { container } = render(<BenchmarkComparison {...actions()} analysis={analysis(saved)} />)
    expect(screen.getByText(/Matched dates only · 4 sessions omitted/)).toBeVisible()
    expect(screen.getByText(/145 matched observations/)).toBeVisible()
    expect(screen.getByText('2 pp')).toBeVisible()
    expect(screen.queryByText('Beta')).not.toBeVisible()
    await userEvent.click(screen.getByText('Benchmark details'))
    expect(screen.getByText('Beta').parentElement).toHaveTextContent('—')
    expect((await axe(container)).violations).toEqual([])
  })

  it.each([
    { readOnly: true },
    { freezeAnalysis: true },
  ])('preserves frozen saved comparisons without download controls: %j', (guard) => {
    const props = actions()
    const { rerender } = render(<BenchmarkComparison {...props} {...guard} />)
    expect(screen.queryByRole('region', { name: 'Market benchmark' })).not.toBeInTheDocument()
    rerender(<BenchmarkComparison {...props} {...guard} analysis={analysis()} />)
    expect(screen.getByText('Against Nifty 50')).toBeVisible()
    expect(
      within(screen.getByRole('region', { name: 'Market benchmark' })).queryByRole('button')
    ).not.toBeInTheDocument()
    expect(props.onPrepare).not.toHaveBeenCalled()
  })

  it('shows unavailable data without inventing benchmark values', () => {
    const saved = benchmark()
    saved.status = 'unavailable'
    saved.reason = 'No common sessions.'
    render(<BenchmarkComparison {...actions()} analysis={analysis(saved)} />)
    expect(screen.getByText('No common sessions.')).toBeVisible()
    expect(screen.queryByText('6%')).not.toBeInTheDocument()
    expect(screen.queryByText('Aligned benchmark chart')).not.toBeInTheDocument()
  })
})
