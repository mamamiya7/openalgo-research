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
import type { ResearchSource } from '@/api/scannerResearch'
import { PortfolioLineChart } from '@/components/portfolio/PortfolioLineChart'
import PortfolioResearch from '@/pages/PortfolioResearch'
import { useAuthStore } from '@/stores/authStore'
import { addPortfolioSource, freshPortfolioDraft, portfolioPayload } from './PortfolioBuilder'
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
    exportUrl: (id: string) => `/scanner-research/api/jobs/${id}/export`,
  },
}))
vi.mock('@/components/portfolio/PortfolioLineChart', () => ({
  PortfolioLineChart: vi.fn(() => <div>Native portfolio chart</div>),
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
})
afterEach(() => {
  for (const client of clients) client.clear()
  clients.length = 0
})

describe('native portfolio consumer journey', () => {
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

  it('opens ranges only for selected parameters and preserves them across mode switches', async () => {
    savedDraft()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Optimize', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Choose settings to optimize' }))
    expect(screen.getByLabelText('Profit target (%)')).toHaveValue(10)
    expect(screen.queryByLabelText('Profit target (%) min')).not.toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Optimize Profit target (%)'))
    expect(screen.getByLabelText('Profit target (%) min')).toHaveValue(5)
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
    expect(await screen.findByRole('progressbar', { name: 'Portfolio progress' })).toHaveAttribute(
      'value',
      '39'
    )
  })

  it('does not submit invalid allocations or optimization with no variable settings', async () => {
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
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose at least one setting')
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

  it('records later-period validation only for optimization without erasing its draft choice', async () => {
    const initial = draft()
    initial.optimizing = true
    initial.portfolio.strategies[0].search.target_pct = { min: 5, max: 15, step: 5 }
    savedDraft(initial)
    mount()
    await userEvent.click(screen.getByLabelText('Check a later period'))
    let stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(portfolioPayload(stored).validation).toEqual({ train_pct: 80 })
    await userEvent.click(screen.getByRole('button', { name: 'Backtest', exact: true }))
    stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(portfolioPayload(stored).validation).toBeUndefined()
    expect(stored.portfolio.validation).toEqual({ train_pct: 80 })
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
    expect(screen.getByText('NautilusTrader 1.221.0')).toBeVisible()
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
  it('leads with marked account metrics and keeps timestamped chart points intact', () => {
    render(
      <PortfolioResults
        job={finished}
        result={result}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    expect(screen.getByText('Final equity', { selector: 'dt' }).parentElement).toHaveTextContent(
      '₹1,01,000'
    )
    expect(screen.getByText('Available cash', { selector: 'dt' }).parentElement).toHaveTextContent(
      '₹99,000'
    )
    expect(screen.getByRole('heading', { name: 'Strategy contributions' })).toBeVisible()
    expect(vi.mocked(PortfolioLineChart).mock.calls.at(-1)?.[0].series[0].data[0].date).toBe(
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
    expect(screen.getByText('Final equity', { selector: 'dt' }).parentElement).toHaveTextContent(
      '₹99,800'
    )
    expect(screen.queryByRole('button', { name: 'Run again' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Trials' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Earlier period', exact: true }))
    expect(screen.getByText('Final equity', { selector: 'dt' }).parentElement).toHaveTextContent(
      '₹1,01,000'
    )
  })
})
