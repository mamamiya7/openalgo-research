import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnalysisChart, PortfolioResult } from '@/api/portfolioResearch'
import { defaultReportPreferences } from '@/api/reportPreferences'
import { AnalysisFigure } from './AnalysisCharts'
import type { AnalysisActions } from './PortfolioAnalysis'
import { PortfolioContinuousReport } from './PortfolioContinuousReport'
import { reportMetrics } from './portfolioReportMetrics'
import { ReportDrawdowns } from './ReportDrawdowns'

vi.mock('./ResearchPlot', () => ({
  default: ({ chart }: { chart: AnalysisChart }) => (
    <output aria-label={`Plot ${chart.id}`}>{JSON.stringify(chart.figure)}</output>
  ),
}))

const chart = (id: string): AnalysisChart => ({
  id,
  title: id,
  status: 'available',
  figure: {
    data: [{ type: 'scatter', mode: 'lines', x: ['2026-01-05T09:16:00+05:30'], y: [2] }],
    layout: { yaxis: { title: '%' } },
  },
})
const fixture = (): PortfolioResult => ({
  config: { initial_capital: 100000 },
  strategies: [],
  per_strategy: [],
  ledger: [],
  summary: {
    initial_capital: 100000,
    final_equity: 100000,
    net_return_pct: 0,
    net_pnl: 0,
    max_drawdown_pct: 4,
    closed_trades: 0,
    win_rate_pct: null,
  },
  equity_curve: [
    {
      date: '2026-01-05',
      timestamp: '2026-01-05T09:16:00+05:30',
      equity: 100000,
      cash: 95000,
      drawdown_pct: 4,
    },
  ],
  analysis: {
    version: 'research-analysis-v2',
    catalog: [
      ...reportMetrics,
      {
        key: 'vectorbt_portfolio_sharpe_ratio',
        label: 'Native Sharpe',
        group: 'VectorBT',
        source: 'VectorBT Portfolio',
        description: 'Native provider definition',
        format: 'number',
      },
    ],
    metrics: {
      account_sharpe_ratio: null,
      vectorbt_portfolio_sharpe_ratio: 9,
      account_annualized_return_pct: 0,
      account_win_rate_pct: 99,
    },
    unavailable: { account_sharpe_ratio: 'Daily return volatility is zero.' },
    basis: ['252 recorded sessions; original evidence preserved.'],
    charts: [
      'account-cumulative',
      'account-underwater',
      'monthly-returns',
      'daily-return-distribution',
      'rolling-sharpe',
      'rolling-volatility',
      'rolling-sharpe-63',
      'rolling-volatility-63',
      'trade-pnl',
      'account-exposure',
    ].map(chart),
  },
})
const actions = (): AnalysisActions => ({
  busy: false,
  response: { status: 'missing' },
  onPrepare: vi.fn(),
  readOnly: false,
  exportUrl: '/analysis/export',
})
const mount = (result = fixture(), overrides: Partial<AnalysisActions> = {}) =>
  render(
    <PortfolioContinuousReport result={result} {...actions()} {...overrides} onTrades={vi.fn()} />
  )

beforeEach(() => vi.stubGlobal('IntersectionObserver', undefined))
afterEach(() => vi.unstubAllGlobals())

describe('continuous portfolio report', () => {
  it('customizes headlines and grouped statistics using exact account or provider keys', async () => {
    const value = fixture()
    const before = JSON.stringify(value)
    mount(value)
    await userEvent.click(screen.getByRole('button', { name: 'Customize', exact: true }))
    const dialog = screen.getByRole('dialog', { name: 'Customize report' })
    await userEvent.click(
      within(dialog).getByRole('checkbox', { name: 'Sharpe · Shared account', exact: true })
    )
    await userEvent.click(
      within(dialog).getByRole('checkbox', {
        name: 'Native Sharpe · VectorBT Portfolio',
        exact: true,
      })
    )
    await userEvent.click(within(dialog).getByRole('tab', { name: 'Beside the chart' }))
    await userEvent.click(
      within(dialog).getByRole('checkbox', {
        name: 'Starting capital · Shared account',
        exact: true,
      })
    )
    await userEvent.click(within(dialog).getByRole('button', { name: 'Apply', exact: true }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const headlines = screen.getByLabelText('Headline statistics')
    expect(
      within(headlines).getByText('Native Sharpe · VectorBT Portfolio').parentElement
    ).toHaveTextContent('9')
    expect(within(headlines).queryByText('Sharpe', { exact: true })).not.toBeInTheDocument()
    expect(
      within(screen.getByRole('region', { name: 'Account statistics' })).queryByText(
        'Starting capital'
      )
    ).not.toBeInTheDocument()
    expect(JSON.stringify(value)).toBe(before)
    expect(screen.getByRole('button', { name: 'Customize', exact: true })).toHaveFocus()
  })

  it('does not replace the visible choices when applying preferences fails', async () => {
    const save = vi.fn().mockResolvedValue(false)
    const preferences = {
      value: {
        headline_metrics: reportMetrics.slice(0, 6).map((metric) => metric.key),
        statistic_metrics: [],
        performance_view: 'return' as const,
        log_equity: false,
        rolling_window: 21 as const,
        expanded_sections: [],
      },
      ready: true,
      saving: false,
      error: null,
      conflict: false,
      save,
      reload: vi.fn(),
    }
    render(
      <PortfolioContinuousReport
        result={fixture()}
        {...actions()}
        onTrades={vi.fn()}
        preferences={preferences}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Customize', exact: true }))
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'Sharpe · Shared account', exact: true })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Apply', exact: true }))
    expect(await screen.findByRole('alert')).toHaveTextContent('not saved')
    expect(screen.getByRole('dialog')).toBeVisible()
    expect(save).toHaveBeenCalledOnce()
  })

  it('keeps choices from another engine until the user explicitly removes them', async () => {
    const save = vi.fn().mockResolvedValue(true)
    const preferences = {
      value: {
        ...defaultReportPreferences,
        headline_metrics: ['account_net_return_pct', 'nautilus_expectancy'],
        statistic_metrics: ['nautilus_returns_skew'],
      },
      ready: true,
      saving: false,
      error: null,
      conflict: false,
      save,
      reload: vi.fn(),
    }
    render(
      <PortfolioContinuousReport
        result={fixture()}
        {...actions()}
        onTrades={vi.fn()}
        preferences={preferences}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Customize', exact: true }))
    expect(
      screen.getByRole('checkbox', { name: 'Keep saved statistic nautilus_expectancy' })
    ).toBeChecked()
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'Sharpe · Shared account', exact: true })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Apply', exact: true }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(save).toHaveBeenLastCalledWith({
      headline_metrics: ['account_net_return_pct', 'nautilus_expectancy', 'account_sharpe_ratio'],
      statistic_metrics: ['nautilus_returns_skew'],
    })
    await userEvent.click(screen.getByRole('button', { name: 'Customize', exact: true }))
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'Keep saved statistic nautilus_expectancy' })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Apply', exact: true }))
    expect(save).toHaveBeenLastCalledWith({
      headline_metrics: ['account_net_return_pct'],
      statistic_metrics: ['nautilus_returns_skew'],
    })
  })

  it('leads with six saved account values, preserving zeros/nulls and keeping native values separate', async () => {
    const { container } = mount()
    const headlines = screen.getByLabelText('Headline statistics')
    expect(within(headlines).getAllByRole('definition')).toHaveLength(6)
    expect(within(headlines).getByText('Net return').parentElement).toHaveTextContent('0%')
    expect(within(headlines).getByText('Closed trades').parentElement).toHaveTextContent('0')
    expect(within(headlines).getByText('Sharpe').parentElement).toHaveTextContent('—')
    expect(within(headlines).getByText('Closed-trade win rate').parentElement).toHaveTextContent(
      '—'
    )
    expect(screen.queryByText('Native Sharpe')).not.toBeInTheDocument()
    await screen.findByLabelText('Plot account-cumulative')
    await act(async () => expect((await axe(container)).violations).toEqual([]))
    await userEvent.click(screen.getByText('All statistics', { selector: 'summary' }))
    expect(screen.getByText('Native Sharpe')).toBeVisible()
    expect(screen.getByText('Daily return volatility is zero.')).not.toBeVisible()
  })

  it('renders coordinated figures together and changes only saved view coordinates', async () => {
    const value = fixture()
    const before = JSON.stringify(value)
    mount(value)
    expect(await screen.findByLabelText('Plot account-cumulative')).toBeVisible()
    expect(screen.getByLabelText('Plot account-underwater')).toBeVisible()
    expect(screen.getByLabelText('Plot monthly-returns')).toBeVisible()
    expect(screen.queryByLabelText('Analysis chart')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Equity', exact: true }))
    const equity = await screen.findByLabelText('Plot report-equity')
    expect(equity).toHaveTextContent('2026-01-05T09:16:00+05:30')
    expect(equity).not.toHaveTextContent('95000')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Log scale' }))
    expect(equity).toHaveTextContent('"type":"log"')
    await userEvent.click(screen.getByRole('button', { name: 'Return %', exact: true }))
    expect(await screen.findByLabelText('Plot account-cumulative')).not.toHaveTextContent(
      '"type":"log"'
    )
    expect(JSON.stringify(value)).toBe(before)
    expect(
      within(screen.getByLabelText('Headline statistics')).getByText('Net return').parentElement
    ).toHaveTextContent('0%')
  })

  it('keeps older results readable and never invents positive equity for a log view', async () => {
    const value = fixture()
    delete value.analysis
    value.equity_curve[0].equity = 0
    mount(value)
    expect(await screen.findByLabelText('Plot account-cumulative')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Prepare analysis' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Consistency' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Equity', exact: true }))
    expect(screen.getByRole('checkbox', { name: 'Log scale' })).toBeDisabled()
    expect(screen.getByLabelText('Plot report-equity')).toHaveTextContent('"y":[0]')
  })

  it('offers saved rolling windows without calculating or requesting another analysis', async () => {
    const prepare = vi.fn()
    mount(fixture(), { onPrepare: prepare })
    await screen.findByLabelText('Plot rolling-sharpe')
    await userEvent.selectOptions(screen.getByLabelText('Rolling window'), '63')
    expect(await screen.findByLabelText('Plot rolling-sharpe-63')).toBeVisible()
    expect(screen.getByLabelText('Plot rolling-volatility-63')).toBeVisible()
    expect(screen.getByRole('option', { name: '126 sessions' })).toBeDisabled()
    expect(prepare).not.toHaveBeenCalled()
  })

  it('updates older analysis explicitly while keeping its saved report visible', async () => {
    const value = fixture()
    value.analysis!.version = 'research-analysis-v1'
    const props = actions()
    const view = render(<PortfolioContinuousReport result={value} {...props} onTrades={vi.fn()} />)
    expect(await screen.findByLabelText('Plot account-cumulative')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Update report' }))
    expect(props.onPrepare).toHaveBeenCalledOnce()
    view.rerender(
      <PortfolioContinuousReport result={value} {...props} readOnly onTrades={vi.fn()} />
    )
    expect(screen.queryByRole('button', { name: 'Update report' })).not.toBeInTheDocument()
    view.rerender(<PortfolioContinuousReport result={fixture()} {...props} onTrades={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Update report' })).not.toBeInTheDocument()
  })

  it('expands a chart and restores focus to its control', async () => {
    mount()
    const trigger = screen.getByRole('button', { name: 'Expand account-cumulative' })
    await userEvent.click(trigger)
    expect(screen.getByRole('dialog')).toHaveTextContent('account-cumulative')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('defers offscreen figures and releases their observer when visible or unmounted', async () => {
    let notify: IntersectionObserverCallback = () => undefined
    const disconnect = vi.fn()
    const observe = vi.fn()
    vi.stubGlobal(
      'IntersectionObserver',
      class {
        constructor(callback: IntersectionObserverCallback) {
          notify = callback
        }
        disconnect = disconnect
        observe = observe
      }
    )
    const view = render(<AnalysisFigure chart={chart('deferred')} deferred />)
    expect(observe).toHaveBeenCalledOnce()
    expect(screen.queryByLabelText('Plot deferred')).not.toBeInTheDocument()
    await act(async () =>
      notify([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)
    )
    expect(await screen.findByLabelText('Plot deferred')).toBeVisible()
    expect(disconnect).toHaveBeenCalled()
    view.unmount()
    const before = disconnect.mock.calls.length
    render(<AnalysisFigure chart={chart('unmounted')} deferred />).unmount()
    expect(disconnect.mock.calls.length).toBeGreaterThan(before)
  })

  it('shows saved drawdown episodes with ongoing recovery unknown and bounded expansion', async () => {
    const drawdowns: Parameters<typeof ReportDrawdowns>[0]['drawdowns'] = {
      status: 'available',
      total: 6,
      shown: 6,
      truncated: false,
      basis: 'Complete saved account marks.',
      duration_definition: 'Recorded sessions below the peak.',
      recovery_definition: 'Sessions after the trough until recovery; ongoing is undefined.',
      rows: Array.from({ length: 6 }, (_, index) => ({
        id: index,
        peak_at: '2026-01-05',
        start_at: '2026-01-06',
        trough_at: '2026-01-07',
        end_at: '2026-01-08',
        recovered_at: index === 0 ? null : '2026-01-08',
        status: index === 0 ? 'ongoing' : 'recovered',
        depth_pct: 5 - index * 0.5,
        peak_equity: 100000,
        trough_equity: 95000,
        underwater_bars: 2,
        underwater_sessions: 2,
        recovery_sessions: index === 0 ? null : 1,
        recovery_days: index === 0 ? null : 1,
        peak_index: 0,
        trough_index: 2,
        end_index: 3,
      })),
    }
    render(<ReportDrawdowns drawdowns={drawdowns} />)
    expect(screen.getAllByRole('row')).toHaveLength(6)
    expect(screen.getByText('Ongoing').closest('tr')).toHaveTextContent('—')
    await userEvent.click(screen.getByRole('button', { name: 'View all' }))
    expect(screen.getAllByRole('row')).toHaveLength(7)
    expect(screen.getByRole('heading', { name: 'Drawdown episodes' })).toBeVisible()
    expect(drawdowns.rows).toHaveLength(6)
  })
})
