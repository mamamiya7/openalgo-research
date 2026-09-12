import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  type PortfolioCapabilities,
  type PortfolioJob,
  type PortfolioResult,
  portfolioResearch,
} from '@/api/portfolioResearch'
import { researchStudyActivity } from '@/api/researchStudyActivity'
import type { ResearchSource } from '@/api/scannerResearch'
import PortfolioResearch from '@/pages/PortfolioResearch'
import { useAuthStore } from '@/stores/authStore'
import {
  addPortfolioSource,
  applySuggestedSearch,
  freshPortfolioDraft,
  portfolioDraftIssue,
  portfolioPayload,
  suggestedStrategySearch,
} from './PortfolioBuilder'
import { PortfolioResults } from './PortfolioResults'

vi.mock('@/api/portfolioResearch', () => ({
  portfolioResearch: {
    capabilities: vi.fn(),
    sources: vi.fn(),
    upload: vi.fn(),
    preflight: vi.fn(),
    submit: vi.fn(),
    job: vi.fn(),
    jobs: vi.fn(),
    cancel: vi.fn(),
    resume: vi.fn(),
    rerun: vi.fn(),
    analysis: vi.fn().mockResolvedValue({ status: 'missing' }),
    prepareAnalysis: vi.fn(),
    analysisExportUrl: (id: string) => `/scanner-research/api/portfolio/jobs/${id}/analysis/export`,
    exportUrl: (id: string) => `/scanner-research/api/jobs/${id}/export`,
  },
}))
vi.mock('@/api/researchStudyActivity', () => ({
  researchStudyActivity: { get: vi.fn() },
}))
vi.mock('@/components/portfolio/PortfolioLineChart', () => ({
  PortfolioLineChart: vi.fn(() => <div>Native portfolio chart</div>),
}))
vi.mock('./ResearchPlot', () => ({
  default: ({ chart }: { chart: { id: string; figure: unknown } }) => (
    <output aria-label={chart.id}>{JSON.stringify(chart.figure)}</output>
  ),
}))
const source = (id: string, interval = 'D'): ResearchSource => ({
  id,
  receipt: {
    input_rows: 4,
    signal_count: 4,
    duplicates_removed: 0,
    date_from: '2026-01-05',
    date_to: '2026-01-08',
    symbol_count: 2,
    warnings: [],
  },
  coverage: {},
  provenance: {
    provider: 'queued-openalgo-history',
    interval,
    exchange: 'NSE',
    adjustment_basis: 'broker',
    calendar_basis: 'native',
    synthetic: false,
  },
})
function draft() {
  return addPortfolioSource(freshPortfolioDraft(), source('a'.repeat(32)), 'Breakout.csv')
}
const account = draft()
const strategy = account.portfolio.strategies[0]
const capabilities: PortfolioCapabilities = {
  max_strategies: 8,
  engines: [
    { id: 'vectorbt', name: 'VectorBT', available: true, intervals: ['D', '1m'] },
    { id: 'nautilus', name: 'NautilusTrader', available: false, intervals: ['D', '1m'] },
  ],
  optimizers: [{ id: 'optuna', available: true, samplers: ['tpe', 'grid'] }],
}
const result: PortfolioResult = {
  portfolio: account.portfolio,
  config: { initial_capital: 100000 },
  strategies: [strategy],
  summary: {
    initial_capital: 100000,
    final_equity: 101000,
    net_pnl: 1000,
    net_return_pct: 1,
    max_drawdown_pct: 0.5,
    closed_trades: 3,
    pending_trades: 0,
    skipped_trades: 0,
  },
  equity_curve: [
    {
      date: '2026-01-06',
      timestamp: '2026-01-06T09:16:00+05:30',
      equity: 101000,
      cash: 99000,
      drawdown_pct: 0.5,
    },
  ],
  ledger: [
    {
      strategy_id: strategy.id,
      strategy_name: strategy.name,
      symbol: 'TCS',
      status: 'closed',
      source_row: 2,
      entry_date: '2026-01-06',
      exit_date: '2026-01-07',
      entry_price: 100,
      exit_price: 110,
      quantity: 10,
      pnl: 100,
      reason: 'target; open exit',
    },
  ],
  per_strategy: [
    {
      id: strategy.id,
      name: strategy.name,
      allocation_pct: 100,
      net_pnl: 1000,
      contribution_pct: 1,
      summary: { closed_trades: 3 },
    },
  ],
  source: { provider: 'OpenAlgo Historify', interval: '1m', signal_count: 4, strategy_count: 1 },
  execution: { engine: 'vectorbt', engine_version: '0.28.5', interval: '1m' },
}
const finished: PortfolioJob = {
  id: 'finished-job',
  kind: 'portfolio_backtest',
  status: 'completed',
  progress: 100,
  created_at: 1,
  specification: { portfolio: account.portfolio },
  result,
}
const running: PortfolioJob = {
  ...finished,
  id: 'running-job',
  result: undefined,
  status: 'running',
  progress: 39,
}
const clients: QueryClient[] = []
function mount(path = '/scanner-research', onLegacyJob?: (id: string) => void) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <PortfolioResearch onLegacyJob={onLegacyJob} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
function savedDraft(value = draft()) {
  sessionStorage.setItem('portfolio-draft:account', JSON.stringify(value))
}
beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  useAuthStore.setState({ user: null })
  vi.mocked(portfolioResearch.capabilities).mockResolvedValue(capabilities)
  vi.mocked(portfolioResearch.jobs).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(portfolioResearch.sources).mockResolvedValue({ items: [], next_offset: null })
  vi.mocked(portfolioResearch.job).mockResolvedValue(running)
  vi.mocked(portfolioResearch.preflight).mockImplementation(async (portfolio) => ({
    portfolio,
    interval: 'D',
    receipt: { ...source('a').receipt, strategy_count: portfolio.strategies.length },
    versions: {},
  }))
  vi.mocked(portfolioResearch.submit).mockResolvedValue(running)
  vi.mocked(portfolioResearch.upload).mockResolvedValue(source('a'.repeat(32)))
  vi.mocked(researchStudyActivity.get).mockResolvedValue({
    version: 'research-study-activity-v1',
    job_id: running.id,
    job_status: 'failed',
    available: false,
    reason: 'not_recorded',
    executions: [],
    executions_truncated: false,
    counts: {
      recorded: 0,
      running: 0,
      evaluated: 0,
      reused: 0,
      allocation_rejected: 0,
      failed: 0,
      cancelled: 0,
      interrupted: 0,
    },
    rows: [],
    next_before: null,
  })
})
afterEach(() => {
  for (const client of clients) client.clear()
  clients.length = 0
})

describe('native portfolio consumer journey', () => {
  it.each([
    'running',
    'failed',
  ])('offers study activity on demand for a %s optimization', async (status) => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      kind: 'portfolio_optimize',
      status,
    })
    vi.mocked(researchStudyActivity.get).mockImplementation(() => new Promise(() => {}))
    mount('/scanner-research?job=running-job')
    const button = await screen.findByRole('button', { name: 'Activity', exact: true })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(researchStudyActivity.get).not.toHaveBeenCalled()
    await userEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(1)
    const signal = vi.mocked(researchStudyActivity.get).mock.calls[0][2]!
    await userEvent.click(button)
    expect(signal.aborted).toBe(true)
    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('does not offer optimizer activity for a backtest run', async () => {
    mount('/scanner-research?job=running-job')
    await screen.findByRole('button', { name: 'Cancel run', exact: true })
    expect(screen.queryByRole('button', { name: 'Activity', exact: true })).not.toBeInTheDocument()
    expect(researchStudyActivity.get).not.toHaveBeenCalled()
  })

  it('limits suggestions to compatible enabled rules and keeps capital, costs and chosen axes unchanged', () => {
    const daily = draft()
    daily.optimization.trials = 70
    daily.portfolio.strategies[0].search = { target_pct: { min: 10, max: 30, step: 10 } }
    const automatic = applySuggestedSearch(daily, true)
    expect(automatic.portfolio.strategies[0].search).toEqual(daily.portfolio.strategies[0].search)
    const suggested = applySuggestedSearch(daily)
    expect(suggested.optimization.trials).toBe(70)
    expect(suggested.portfolio.strategies[0].search.target_pct).toEqual({
      min: 10,
      max: 30,
      step: 10,
    })
    expect(suggested.portfolio.strategies[0].search.hold_sessions).toEqual({
      min: 3,
      max: 7,
      step: 2,
    })
    expect(suggested.portfolio.strategies[0].config).toEqual(daily.portfolio.strategies[0].config)
    expect(suggested.portfolio.capital).toBe(daily.portfolio.capital)
    expect(suggested.portfolio.strategies[0].search.order_size_pct).toBeUndefined()
    expect(suggested.portfolio.strategies[0].search.allocation_pct).toBeUndefined()
    const timed = structuredClone(daily.portfolio.strategies[0])
    timed.config = {
      ...timed.config,
      trade_horizon: 'intraday',
      hold_minutes: 60,
      target_pct: 0,
      stop_pct: 0,
      trailing_enabled: true,
      trailing_pct: 2,
    }
    const minute = suggestedStrategySearch(timed, 'vectorbt')
    expect(minute).toEqual({
      hold_minutes: { min: 30, max: 90, step: 30 },
      trailing_pct: { min: 1, max: 3, step: 1 },
    })
    expect(suggestedStrategySearch(timed, 'nautilus').trailing_pct).toBeUndefined()
    timed.config.hold_minutes = null
    timed.config.trailing_enabled = false
    expect(suggestedStrategySearch(timed, 'vectorbt')).toEqual({})
    const empty = {
      ...daily,
      optimizing: true,
      portfolio: { ...daily.portfolio, strategies: [{ ...timed, search: {} }] },
    }
    expect(portfolioDraftIssue(empty)).toContain('Choose at least one setting')
  })

  it('starts new CSV optimizations with 25 trials and a visible complete suggested search', async () => {
    savedDraft()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Optimize', exact: true }))
    expect(screen.getByLabelText('Trials')).toHaveValue(25)
    expect(screen.getByRole('button', { name: 'Suggested search' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'Optimization ranges' })).toHaveTextContent(
      'Holding sessions 3–7 (step 2)'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Run optimization' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.preflight).mock.calls[0][0]
    expect(sent.optimization).toMatchObject({ trials: 25, sampler: 'tpe', objective: 'balanced' })
    expect(sent.strategies[0].search).toEqual({
      hold_sessions: { min: 3, max: 7, step: 2 },
      target_pct: { min: 5, max: 15, step: 5 },
      stop_pct: { min: 2.5, max: 7.5, step: 2.5 },
    })
  })

  it('names old inputs by count and saved time without guessing they are duplicates', async () => {
    const old = {
      ...source('c'.repeat(32)),
      created_at: 1788958800,
      receipt: {
        ...source('c').receipt,
        signal_count: 1,
        symbol_count: 1,
        date_from: '2026-01-05',
        date_to: '2026-01-05',
      },
    }
    vi.mocked(portfolioResearch.sources).mockResolvedValue({
      items: [old, { ...old, id: 'd'.repeat(32), created_at: old.created_at + 60 }],
      next_offset: null,
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    const dialog = await screen.findByRole('dialog', { name: 'Saved signals' })
    const rows = await within(dialog).findAllByRole('button', { name: /1 signal/ })
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).not.toEqual(rows[1].textContent)
    for (const row of rows) {
      expect(row).toHaveTextContent('1 symbol')
      expect(row).toHaveTextContent('Saved ')
      expect(row).not.toHaveTextContent('1 signals')
      expect(row).not.toHaveTextContent('1 symbols')
      expect(row).not.toHaveTextContent('2026-01-05 – 2026-01-05')
    }
    await userEvent.click(rows[0])
    expect(screen.getByLabelText('Strategy 1 name')).toHaveValue('1 signal')
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.portfolio.strategies[0].source_id).toBe(old.id)
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
  })

  it('uses a retained filename when adding a saved strategy', async () => {
    const named = {
      ...source('c'.repeat(32)),
      receipt: { ...source('c').receipt, filename: 'My breakout.csv' },
    }
    vi.mocked(portfolioResearch.sources).mockResolvedValue({ items: [named], next_offset: null })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    await userEvent.click(await screen.findByRole('button', { name: /My breakout\.csv/ }))
    expect(screen.getByLabelText('Strategy 1 name')).toHaveValue('My breakout')
    expect(screen.getByText('My breakout.csv · 4 signals')).toBeVisible()
    expect(portfolioResearch.upload).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
  })

  it('summarizes an imported Chartink source with signal, symbol and date coverage', async () => {
    const imported = draft()
    const receipt = imported.sources[imported.portfolio.strategies[0].source_id].receipt
    Object.assign(receipt, {
      filename: 'NKS BEST BUY STOCKS FOR INTRADAY.csv',
      signal_count: 365,
      symbol_count: 111,
      date_from: '2026-01-22',
      date_to: '2026-09-08',
      chartink: {
        url: 'https://chartink.com/screener/nks-best-buy',
        title: 'NKS BEST BUY STOCKS FOR INTRADAY',
        selected_period: '9 months',
        captured_at: '2026-09-11T06:00:00Z',
        repaints: true,
        export_kind: 'chartink_history_csv',
      },
    })
    savedDraft(imported)
    mount()
    expect(screen.getByText('365 signals · 111 symbols · 22 Jan–8 Sept 2026')).toBeVisible()
    expect(screen.queryByText(/NKS BEST BUY STOCKS FOR INTRADAY\.csv/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Chartink history' })).toHaveAttribute(
      'href',
      'https://chartink.com/screener/nks-best-buy'
    )
    expect(screen.getByText('· 9 months')).toBeVisible()
    expect(screen.getByText('· Repainting scanner')).toBeVisible()
    expect(portfolioResearch.upload).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
  })

  it('adds saved signals by their original source ID without uploading or preparing prices', async () => {
    const raw = {
      ...source('c'.repeat(32)),
      receipt: { ...source('c').receipt, name: 'Quick signals' },
    }
    const combined = {
      ...source('d'.repeat(32)),
      receipt: { ...source('d').receipt, name: 'Combined portfolio', input_type: 'portfolio' },
    }
    vi.mocked(portfolioResearch.sources).mockResolvedValue({
      items: [raw, combined],
      next_offset: null,
    })
    mount()
    expect(portfolioResearch.sources).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    const dialog = await screen.findByRole('dialog', { name: 'Saved signals' })
    const item = await within(dialog).findByRole('button', { name: /Quick signals/ })
    expect(item).toHaveTextContent('2026-01-05 – 2026-01-08')
    expect(item).toHaveTextContent('4 signals')
    expect(item).toHaveTextContent('2 symbols')
    expect(within(dialog).queryByText('Combined portfolio')).not.toBeInTheDocument()
    await userEvent.dblClick(item)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Strategy 1 name')).toHaveValue('Quick signals')
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.portfolio.strategies).toHaveLength(1)
    expect(stored.portfolio.strategies[0].source_id).toBe(raw.id)
    expect(stored.sources[raw.id].receipt.signal_count).toBe(4)
    expect(portfolioResearch.upload).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Use saved signals' })).toHaveFocus()
    )
  })

  it('pages saved inputs and keeps cached source choices scoped to the account', async () => {
    const first = {
      ...source('c'.repeat(32)),
      receipt: { ...source('c').receipt, name: 'First page' },
    }
    const next = {
      ...source('d'.repeat(32)),
      receipt: { ...source('d').receipt, name: 'Next page' },
    }
    vi.mocked(portfolioResearch.sources).mockImplementation(async (offset) =>
      offset === 20 ? { items: [next], next_offset: null } : { items: [first], next_offset: 20 }
    )
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    expect(await screen.findByRole('button', { name: /First page/ })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'More signals' }))
    expect(await screen.findByRole('button', { name: /Next page/ })).toBeVisible()
    expect(portfolioResearch.sources).toHaveBeenLastCalledWith(20, expect.any(AbortSignal))
    vi.mocked(portfolioResearch.sources).mockResolvedValue({ items: [], next_offset: null })
    await act(async () =>
      useAuthStore.setState({
        user: { username: 'other', broker: null, isLoggedIn: true, loginTime: null },
      })
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    expect(
      await screen.findByText('No saved signals on this page. Upload a CSV to add a strategy.')
    ).toBeVisible()
    expect(screen.queryByRole('button', { name: /First page|Next page/ })).not.toBeInTheDocument()
    expect(portfolioResearch.sources).toHaveBeenLastCalledWith(0, expect.any(AbortSignal))
  })

  it('caps saved-source additions at eight even after repeated selection events', async () => {
    let initial = freshPortfolioDraft()
    for (let index = 0; index < 7; index++)
      initial = addPortfolioSource(initial, source('a'.repeat(32)), `Strategy ${index + 1}.csv`)
    savedDraft(initial)
    vi.mocked(portfolioResearch.sources).mockResolvedValue({
      items: [
        { ...source('b'.repeat(32)), receipt: { ...source('b').receipt, name: 'Eighth signals' } },
      ],
      next_offset: null,
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    await userEvent.dblClick(await screen.findByRole('button', { name: /Eighth signals/ }))
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.portfolio.strategies).toHaveLength(8)
    expect(new Set(stored.portfolio.strategies.map((row: { id: string }) => row.id)).size).toBe(8)
    expect(screen.queryByRole('button', { name: 'Use saved signals' })).not.toBeInTheDocument()
    expect(addPortfolioSource(stored, source('c'.repeat(32)), 'Too many.csv')).toBe(stored)
  })

  it('keeps the saved-signals picker accessible and returns focus on dismissal', async () => {
    mount()
    const opener = screen.getByRole('button', { name: 'Use saved signals' })
    await userEvent.click(opener)
    await screen.findByText('No saved signals on this page. Upload a CSV to add a strategy.')
    await act(async () => expect((await axe(document.body)).violations).toEqual([]))
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(opener).toHaveFocus())
  })

  it('starts with one upload action, shared cash, and no legacy or engine controls', () => {
    mount()
    expect(screen.getByRole('heading', { name: 'Backtest & Optimize' })).toBeVisible()
    expect(screen.getByLabelText('Shared starting cash (₹)')).toHaveValue(100000)
    expect(screen.getByRole('button', { name: 'Add strategy' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeDisabled()
    for (const label of [
      'Backtesting engine',
      'Scanner-count trigger',
      'Candle interval',
      'Profit target (%)',
    ])
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    expect(screen.queryByText('Prepare prices')).not.toBeInTheDocument()
    expect(screen.getByText('CSV format & examples').closest('details')).not.toHaveAttribute('open')
  })

  it.each([
    ['daily', 'D', 'Date,Symbol'],
    ['timed', '1m', 'Date,Time,Symbol'],
  ])('lets a new user inspect and upload the %s CSV without preparing prices', async (kind, interval, header) => {
    vi.mocked(portfolioResearch.upload).mockResolvedValue(source('a'.repeat(32), interval))
    mount()
    await userEvent.click(screen.getByText('CSV format & examples'))
    expect(screen.getByText(/Illustrative signals only/)).toBeVisible()
    const download = screen.getByRole('link', { name: `Download ${kind} example` })
    expect(download).toHaveAttribute('download', `example-${kind}-signals.csv`)
    const csv = decodeURIComponent(download.getAttribute('href')!.split(',').slice(1).join(','))
    expect(csv.split('\r\n')[0]).toBe(header)
    expect(portfolioResearch.upload).not.toHaveBeenCalled()
    expect(portfolioResearch.sources).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
    if (kind === 'daily')
      await act(async () => expect((await axe(document.body)).violations).toEqual([]))

    const file = new File([csv], download.getAttribute('download')!, { type: 'text/csv' })
    await userEvent.upload(screen.getByLabelText('Upload strategy CSV files'), file)
    expect(await screen.findByLabelText('Strategy 1 name')).toHaveValue(`example-${kind}-signals`)
    expect(portfolioResearch.upload).toHaveBeenCalledWith(file)
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Candle interval')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const submitted = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(submitted.strategies[0].source_id).toBe('a'.repeat(32))
    expect(submitted.strategies[0].config.hold_minutes).toBeUndefined()
    expect(submitted.strategies[0].config.trade_horizon).toBeUndefined()
    expect(submitted).not.toHaveProperty('interval')
  })

  it('uploads two CSV strategies and splits untouched allocations equally', async () => {
    vi.mocked(portfolioResearch.upload)
      .mockResolvedValueOnce(source('a'.repeat(32)))
      .mockResolvedValueOnce(source('b'.repeat(32)))
    mount()
    await userEvent.upload(screen.getByLabelText('Upload strategy CSV files'), [
      new File(['date,symbol'], 'Breakout.csv', { type: 'text/csv' }),
      new File(['date,symbol'], 'Momentum.csv', { type: 'text/csv' }),
    ])
    expect(await screen.findByLabelText('Allocation for Breakout (%)')).toHaveValue(50)
    expect(screen.getByLabelText('Allocation for Momentum (%)')).toHaveValue(50)
    expect(screen.getByText('100% allocated')).toBeVisible()
    expect(portfolioResearch.upload).toHaveBeenCalledTimes(2)
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
  })

  it('shows confirmed file progress while the next CSV is still being read', async () => {
    let completeFirst!: (value: ResearchSource) => void
    let completeSecond!: (value: ResearchSource) => void
    vi.mocked(portfolioResearch.upload)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            completeFirst = resolve
          })
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            completeSecond = resolve
          })
      )
    mount()
    await userEvent.upload(screen.getByLabelText('Upload strategy CSV files'), [
      new File(['Symbol,Date\nINFY,2026-01-05'], 'First.csv', { type: 'text/csv' }),
      new File(['Symbol,Date\nTCS,2026-01-05'], 'Second.csv', { type: 'text/csv' }),
    ])
    expect(screen.getByText('Reading CSV 1 of 2')).toBeVisible()
    expect(screen.getByRole('progressbar', { name: 'Reading CSV file' })).not.toHaveAttribute(
      'value'
    )
    expect(screen.queryByText(/signals accepted/)).not.toBeInTheDocument()
    await act(async () => completeFirst(source('a'.repeat(32))))
    expect(screen.getByText('Reading CSV 2 of 2')).toBeVisible()
    expect(screen.getByText('1 file processed · 4 signals accepted')).toBeVisible()
    expect(screen.getByText('Second.csv')).toBeVisible()
    await act(async () => completeSecond(source('b'.repeat(32))))
    expect(screen.queryByRole('progressbar', { name: 'Reading CSV file' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Strategy 1 name')).toHaveValue('First')
    expect(screen.getByLabelText('Strategy 2 name')).toHaveValue('Second')
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
  })

  it('preserves chosen weights when another strategy is added and offers explicit equal split', async () => {
    savedDraft()
    mount()
    fireEvent.change(screen.getByLabelText('Allocation for Breakout (%)'), {
      target: { value: '30' },
    })
    vi.mocked(portfolioResearch.upload).mockResolvedValueOnce(source('b'.repeat(32)))
    await userEvent.upload(
      screen.getByLabelText('Upload strategy CSV files'),
      new File(['date,symbol'], 'Momentum.csv', { type: 'text/csv' })
    )
    expect(await screen.findByLabelText('Allocation for Momentum (%)')).toHaveValue(70)
    expect(screen.getByLabelText('Allocation for Breakout (%)')).toHaveValue(30)
    await userEvent.click(screen.getByRole('button', { name: 'Equal split' }))
    expect(screen.getByLabelText('Allocation for Breakout (%)')).toHaveValue(50)
  })

  it('shows suggested ranges, lets the user opt out of one, and preserves choices across mode switches', async () => {
    savedDraft()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Optimize', exact: true }))
    expect(screen.getByRole('region', { name: 'Optimization ranges' })).toHaveTextContent(
      'Profit target 5–15% (step 5)'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Edit search for Breakout' }))
    expect(screen.getByLabelText('Profit target (%) min')).toHaveValue(5)
    expect(screen.getByLabelText('Stop loss (%) min')).toHaveValue(2.5)
    await userEvent.click(screen.getByLabelText('Optimize Stop loss (%)'))
    expect(screen.queryByLabelText('Stop loss (%) min')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Profit target (%) max'), { target: { value: '20' } })
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    await userEvent.click(screen.getByRole('button', { name: 'Backtest', exact: true }))
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(portfolioPayload(stored).strategies[0].search).toEqual({})
    expect(stored.portfolio.strategies[0].search.target_pct.max).toBe(20)
    await userEvent.click(screen.getByRole('button', { name: 'Optimize', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Settings for Breakout' }))
    expect(screen.getByLabelText('Profit target (%) max')).toHaveValue(20)
    expect(screen.queryByLabelText('Stop loss (%) min')).not.toBeInTheDocument()
  })

  it('uses intraday requirements only when selected and keeps candle choice automatic', async () => {
    savedDraft()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Settings for Breakout' }))
    await userEvent.selectOptions(screen.getByLabelText('Holding period'), 'intraday')
    expect(screen.getByLabelText('Maximum holding minutes')).toHaveValue(60)
    expect(screen.queryByLabelText('Maximum holding sessions')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Candle interval')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalled())
    expect(vi.mocked(portfolioResearch.submit).mock.calls[0][0].strategies[0].config).toMatchObject(
      { trade_horizon: 'intraday', hold_minutes: 60 }
    )
  })

  it('validates and submits one full portfolio job from the only Run button', async () => {
    savedDraft()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    expect(portfolioResearch.preflight).toHaveBeenCalledTimes(1)
    const [payload, requestId] = vi.mocked(portfolioResearch.submit).mock.calls[0]
    expect(payload.engine).toBe('vectorbt')
    expect(payload.optimization).toBeUndefined()
    expect(payload.strategies[0].source_id).toBe('a'.repeat(32))
    expect(requestId).toBeTruthy()
    expect(
      await screen.findByRole('progressbar', { name: 'Running your backtest' })
    ).not.toHaveAttribute('value')
  })

  it('rejects invalid allocations and runs the visible suggested optimization after correcting them', async () => {
    const invalid = addPortfolioSource(draft(), source('b'.repeat(32)), 'Momentum.csv')
    invalid.portfolio.strategies.forEach((item) => {
      item.allocation_pct = 70
    })
    savedDraft(invalid)
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('at most 100%')
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Equal split' }))
    await userEvent.click(screen.getByRole('button', { name: 'Optimize', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Run optimization' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(portfolioResearch.preflight).mock.calls[0][0]
    expect(payload.optimization?.trials).toBe(25)
    expect(payload.strategies.every((item) => Object.keys(item.search).length > 0)).toBe(true)
  })

  it('keeps the same request identity after an uncertain submission error', async () => {
    savedDraft()
    vi.mocked(portfolioResearch.submit)
      .mockRejectedValueOnce(new Error('Connection interrupted'))
      .mockResolvedValueOnce(running)
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Connection interrupted')
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(2))
    expect(vi.mocked(portfolioResearch.submit).mock.calls[0][1]).toBe(
      vi.mocked(portfolioResearch.submit).mock.calls[1][1]
    )
  })

  it('offers cancellation and checkpoint resume without a new-price control', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue(running)
    vi.mocked(portfolioResearch.cancel).mockResolvedValue({
      ...running,
      status: 'cancel_requested',
    })
    const first = mount('/scanner-research?job=running-job')
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel run' }))
    expect(portfolioResearch.cancel).toHaveBeenCalledWith('running-job')
    first.unmount()
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      status: 'interrupted',
      resumable: true,
    })
    vi.mocked(portfolioResearch.resume).mockResolvedValue(running)
    mount('/scanner-research?job=running-job')
    await userEvent.click(await screen.findByRole('button', { name: 'Resume run' }))
    expect(portfolioResearch.resume).toHaveBeenCalledWith('running-job')
  })

  it('opens saved portfolios and routes earlier runs through the legacy callback', async () => {
    const earlier = {
      ...finished,
      id: 'old-job',
      kind: 'backtest',
      title: 'Earlier fixed backtest',
      specification: {},
      result: undefined,
    }
    vi.mocked(portfolioResearch.jobs).mockResolvedValue({
      items: [finished, earlier],
      next_cursor: null,
    })
    const legacy = vi.fn()
    mount('/scanner-research', legacy)
    await userEvent.click(screen.getByRole('button', { name: 'Saved runs' }))
    const modal = await screen.findByRole('dialog')
    expect(await within(modal).findByRole('button', { name: /My portfolio/ })).toBeVisible()
    expect(
      within(modal).queryByRole('button', { name: /Earlier fixed backtest/ })
    ).not.toBeInTheDocument()
    await userEvent.click(within(modal).getByRole('button', { name: 'Earlier runs' }))
    await userEvent.click(within(modal).getByRole('button', { name: /Earlier fixed backtest/ }))
    expect(legacy).toHaveBeenCalledWith('old-job')
  })

  it('removes unused source receipts and keeps drafts scoped to the account', async () => {
    savedDraft()
    sessionStorage.setItem('portfolio-draft:other', JSON.stringify(draft()))
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Remove Breakout' }))
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.sources).toEqual({})
    expect(
      JSON.parse(sessionStorage.getItem('portfolio-draft:other')!).portfolio.strategies
    ).toHaveLength(1)
  })

  it('reserves later data before both baseline and optimization without erasing the choice', async () => {
    const initial = draft()
    initial.optimizing = true
    initial.portfolio.strategies[0].search.target_pct = { min: 5, max: 15, step: 5 }
    savedDraft(initial)
    mount()
    await userEvent.click(screen.getByLabelText('Reserve a later period'))
    let stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(portfolioPayload(stored).validation).toEqual({ train_pct: 80, mode: 'reserve' })
    await userEvent.click(screen.getByRole('button', { name: 'Backtest', exact: true }))
    stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(portfolioPayload(stored).validation).toEqual({ train_pct: 80, mode: 'reserve' })
    expect(stored.portfolio.validation).toEqual({ train_pct: 80, mode: 'reserve' })
  })

  it('preserves legacy validation request semantics and unreserved full-period drafts', () => {
    const initial = draft()
    expect(portfolioPayload(initial).validation).toBeUndefined()
    initial.portfolio.validation = { train_pct: 80 }
    initial.optimizing = false
    expect(portfolioPayload(initial).validation).toBeUndefined()
    initial.optimizing = true
    expect(portfolioPayload(initial).validation).toEqual({ train_pct: 80 })
  })
})

describe('portfolio engine capabilities', () => {
  function allowNautilus() {
    vi.mocked(portfolioResearch.capabilities).mockResolvedValue({
      ...capabilities,
      engines: capabilities.engines.map((engine) => ({ ...engine, available: true })),
    })
  }

  it('offers Nautilus inside More settings only when its runtime is available', async () => {
    allowNautilus()
    savedDraft()
    mount()
    const picker = await screen.findByLabelText('Backtest engine')
    expect(picker).not.toBeVisible()
    await userEvent.click(screen.getByText('More settings'))
    expect(picker).toBeVisible()
    await userEvent.selectOptions(picker, 'nautilus')
    expect(screen.getByText('A different fill model; results can differ.')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalled())
    expect(vi.mocked(portfolioResearch.submit).mock.calls[0][0].engine).toBe('nautilus')
  })

  it('preserves incompatible settings and blocks execution instead of changing them', async () => {
    allowNautilus()
    const initial = draft()
    initial.portfolio.strategies[0].config.slippage_bps = 5
    initial.portfolio.strategies[0].config.trailing_enabled = true
    initial.portfolio.strategies[0].config.trailing_pct = 3
    savedDraft(initial)
    mount()
    await screen.findByLabelText('Backtest engine')
    await userEvent.click(screen.getByText('More settings'))
    await userEvent.selectOptions(screen.getByLabelText('Backtest engine'), 'nautilus')
    expect(screen.getByRole('alert')).toHaveTextContent(
      'set slippage to 0 and turn off trailing protection'
    )
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeDisabled()
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.portfolio.strategies[0].config).toMatchObject({
      slippage_bps: 5,
      trailing_enabled: true,
      trailing_pct: 3,
    })
    expect(stored.portfolio.strategies[0].source_id).toBe(initial.portfolio.strategies[0].source_id)
    await userEvent.selectOptions(screen.getByLabelText('Backtest engine'), 'vectorbt')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeEnabled()
  })

  it('keeps a saved unavailable engine selected and requires cent-precise cash for Nautilus', async () => {
    const initial = draft()
    initial.portfolio.engine = 'nautilus'
    savedDraft(initial)
    const view = mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('NautilusTrader is unavailable')
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeDisabled()
    expect(JSON.parse(sessionStorage.getItem('portfolio-draft:account')!).portfolio.engine).toBe(
      'nautilus'
    )
    view.unmount()
    allowNautilus()
    initial.portfolio.capital = 100000.123
    savedDraft(initial)
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('at most two decimal places')
  })

  it('records the actual engine in result Settings', async () => {
    render(
      <PortfolioResults
        job={finished}
        result={{ ...result, execution: { engine: 'nautilus', engine_version: '1.221.0' } }}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    await userEvent.click(screen.getByRole('tab', { name: 'Settings' }))
    expect(screen.getByText('NautilusTrader 1.221.0', { selector: 'p' })).toBeVisible()
  })

  it('keeps builder and settings drawer accessible with labelled controls and focus return', async () => {
    savedDraft()
    const view = mount()
    await act(async () => {
      expect((await axe(view.container)).violations).toEqual([])
    })
    const button = screen.getByRole('button', { name: 'Settings for Breakout' })
    await userEvent.click(button)
    await act(async () => {
      expect((await axe(document.body)).violations).toEqual([])
    })
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(button).toHaveFocus())
  })
})

describe('portfolio result reading', () => {
  it('keeps reserved dates and exact later evaluation separate from the displayed baseline', async () => {
    const evaluate = vi.fn()
    const view = render(
      <PortfolioResults
        job={finished}
        result={{
          ...result,
          reserved_evaluation: {
            version: 'research-period-plan-v1',
            status: 'reserved',
            selection: { from: '2026-01-05', to: '2026-01-20' },
            evaluation: { from: '2026-02-01', to: '2026-02-05' },
          },
        }}
        onRerun={vi.fn()}
        onEvaluate={evaluate}
        rerunning={false}
        exportUrl="/export"
        embedded
      />
    )
    expect(screen.getByText(/Later period reserved/)).toHaveTextContent('2026-02-01')
    expect(screen.queryByRole('heading', { name: 'My portfolio' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Test later period' }))
    expect(evaluate).toHaveBeenCalledOnce()
    expect(
      screen.queryByRole('button', { name: 'Later period', exact: true })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Report' })).toHaveAttribute('aria-selected', 'true')
    view.unmount()
  })

  it('keeps the trial page when switching away from Trials and back', async () => {
    const selected: PortfolioResult = {
      ...result,
      experiment: {
        kind: 'portfolio_optimize',
        rows: Array.from({ length: 65 }, (_, index) => ({
          trial_number: index,
          config_id: `trial-${index}`,
          strategies: [strategy],
          summary: result.summary,
          score: 0.5,
          stage: 'tpe',
        })),
        recommendation_id: 'trial-0',
        selected_strategies: [strategy],
        specification: { sampler: 'tpe', trials: 100, objective: 'balanced', seed: 0 },
        optimizer: {
          sampler: 'TPESampler',
          objective_definition: 'return minus drawdown',
          version: '5',
        },
        counts: { evaluated_this_pass: 65, rejected_allocations: 0, reused_trials: 35 },
      },
    }
    render(
      <PortfolioResults
        job={finished}
        result={selected}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    await userEvent.click(screen.getByRole('tab', { name: 'Trials' }))
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('26–50 of 65')).toBeVisible()
    await userEvent.click(screen.getByRole('tab', { name: 'Report' }))
    await userEvent.click(screen.getByRole('tab', { name: 'Trials' }))
    expect(screen.getByText('26–50 of 65')).toBeVisible()
  })

  it('leads with marked account metrics and keeps timestamped chart points intact', async () => {
    render(
      <PortfolioResults
        job={finished}
        result={result}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    expect(
      screen.getByText('Final equity', { selector: 'summary' }).closest('dt')?.parentElement
    ).toHaveTextContent('₹1,01,000')
    expect(screen.getByText('Available cash', { selector: 'dt' }).parentElement).toHaveTextContent(
      '₹99,000'
    )
    expect(screen.getByRole('heading', { name: 'Strategy contributions' })).toBeVisible()
    expect(await screen.findByLabelText('account-cumulative')).toHaveTextContent(
      '2026-01-06T09:16:00+05:30'
    )
    expect(screen.queryByText('target; open exit')).not.toBeInTheDocument()
  })

  it('opens per-strategy trades and paginates the ledger instead of rendering everything', async () => {
    const many = {
      ...result,
      ledger: Array.from({ length: 55 }, (_, index) => ({
        ...result.ledger[0],
        source_row: index + 2,
        symbol: `TEST${index}`,
      })),
    }
    render(
      <PortfolioResults
        job={finished}
        result={many}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Breakout' }))
    expect(screen.getByLabelText('Filter trades by strategy')).toHaveValue(strategy.id)
    expect(screen.getByText('TEST24')).toBeVisible()
    expect(screen.queryByText('TEST25')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('TEST25')).toBeVisible()
    expect(screen.queryByText('TEST0')).not.toBeInTheDocument()
  })

  it('shows one partial-coverage line and exact candidate rerun controls', async () => {
    const selected: PortfolioResult = {
      ...result,
      source: { ...result.source!, excluded_signals: 1 },
      experiment: {
        kind: 'portfolio_optimize',
        rows: [
          {
            trial_number: 0,
            config_id: 'exact-trial',
            strategies: [strategy],
            summary: result.summary,
            score: 0.5,
            stage: 'tpe',
          },
        ],
        recommendation_id: 'exact-trial',
        selected_strategies: [strategy],
        specification: { sampler: 'tpe', trials: 50, objective: 'balanced', seed: 0 },
        optimizer: {
          sampler: 'TPESampler',
          objective_definition: 'return minus drawdown',
          version: '5.0.0',
        },
        counts: { evaluated_this_pass: 1, rejected_allocations: 2, reused_trials: 0 },
      },
    }
    const rerun = vi.fn()
    render(
      <PortfolioResults
        job={finished}
        result={selected}
        onRerun={rerun}
        rerunning={false}
        exportUrl="/export"
      />
    )
    expect(screen.getAllByText('1 signal excluded — view reasons')).toHaveLength(1)
    await userEvent.click(screen.getByRole('tab', { name: 'Trials' }))
    await userEvent.click(screen.getByRole('button', { name: 'Backtest this' }))
    expect(rerun).toHaveBeenCalledWith('exact-trial')
    await userEvent.click(screen.getByRole('button', { name: 'View settings' }))
    expect(screen.getByRole('region', { name: 'Trial strategy settings' })).toHaveTextContent(
      'Profit target'
    )
  })

  it('keeps later-period results separate from earlier search trials', async () => {
    const validationResult: PortfolioResult = {
      ...result,
      summary: { ...result.summary, final_equity: 99800, net_pnl: -200 },
      equity_curve: [
        { ...result.equity_curve[0], date: '2026-02-01', timestamp: undefined, equity: 99800 },
      ],
    }
    const combined: PortfolioResult = {
      ...result,
      validation: {
        train_from: '2026-01-05',
        train_to: '2026-01-20',
        test_from: '2026-02-01',
        test_to: '2026-02-05',
        training_signals: 20,
        testing_signals: 5,
        label: 'Later period',
        selection_basis: 'settings chosen on earlier period only',
        result: validationResult,
      },
    }
    render(
      <PortfolioResults
        job={finished}
        result={combined}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Later period', exact: true }))
    expect(
      screen.getByText('Final equity', { selector: 'summary' }).closest('dt')?.parentElement
    ).toHaveTextContent('₹99,800')
    expect(screen.queryByRole('button', { name: 'Run again' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Trials' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Earlier period', exact: true }))
    expect(
      screen.getByText('Final equity', { selector: 'summary' }).closest('dt')?.parentElement
    ).toHaveTextContent('₹1,01,000')
  })
})
