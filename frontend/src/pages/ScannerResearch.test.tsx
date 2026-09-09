import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { scannerResearch } from '@/api/scannerResearch'
import { PortfolioLineChart } from '@/components/portfolio/PortfolioLineChart'
import { freshDraft, researchDefaults, writeDraft } from '@/lib/researchDraft'
import ScannerResearch from './ScannerResearch'

vi.mock('@/api/scannerResearch', () => ({
  scannerResearch: {
    jobs: vi.fn(),
    health: vi.fn(),
    capabilities: vi.fn(),
    retry: vi.fn(),
    updateEvidence: vi.fn(),
    sources: vi.fn(),
    source: vi.fn(),
    preflight: vi.fn(),
    resume: vi.fn(),
    job: vi.fn(),
    upload: vi.fn(),
    submit: vi.fn(),
    cancel: vi.fn(),
    exportUrl: (id: string) => `/scanner-research/api/jobs/${id}/export`,
  },
}))
vi.mock('@/components/portfolio/PortfolioLineChart', () => ({
  PortfolioLineChart: vi.fn(() => <div>Daily marked equity chart</div>),
}))
const config = {
  ...researchDefaults,
  initial_capital: 100000,
  order_size_pct: 10,
  target_pct: 10,
  stop_pct: 5,
  hold_sessions: 5,
  cost_bps: 10,
  slippage_bps: 0,
}
const source = {
  id: 'source-1',
  receipt: {
    input_rows: 2,
    signal_count: 2,
    duplicates_removed: 0,
    date_from: '2026-01-05',
    date_to: '2026-01-06',
    symbol_count: 2,
    warnings: [],
  },
  coverage: { status: 'ready' },
  provenance: {
    provider: 'synthetic fixture',
    exchange: 'NSE',
    interval: 'D',
    adjustment_basis: 'synthetic',
    calendar_basis: 'weekdays only',
    synthetic: true,
  },
}
const saved = {
  id: 'saved-123',
  source_id: 'source-1',
  status: 'completed',
  progress: 100,
  created_at: '2026-09-06T12:00:00',
  config,
  result: {
    config,
    policy_version: 'daily-v1',
    metric_basis: 'daily_marked',
    summary: { final_equity: 100400, pending_trades: 1 },
    equity_curve: [{ date: '2026-01-06', equity: 100400, cash: 90000 }],
    ledger: [
      { symbol: 'TCS', status: 'pending', reason: 'Missing candle cannot prove no stop touch' },
    ],
    coverage: { status: 'warning' },
    limits: ['Synthetic fixture; not investment evidence.'],
  },
}
function mount(path = '/scanner-research') {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <ScannerResearch />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
function recoverLegacyDraft() {
  writeDraft('account', { ...freshDraft(), execution: undefined })
}
it('includes signals awaiting entry in the pending total', async () => {
  vi.mocked(scannerResearch.job).mockResolvedValue({
    ...saved,
    result: {
      ...saved.result,
      summary: { ...saved.result.summary, unfunded_pending: 2 },
    },
  })
  mount('/scanner-research?job=saved-123')
  const label = await screen.findByText('Pending', { selector: 'dt' })
  expect(label.parentElement).toHaveTextContent('3')
})
beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  vi.mocked(scannerResearch.jobs).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(scannerResearch.health).mockResolvedValue({
    worker_state: 'online',
    worker_online: true,
    maintenance: false,
    last_heartbeat: 123,
    active_jobs: 0,
  })
  vi.mocked(scannerResearch.capabilities).mockResolvedValue({
    public: {
      available: true,
      date_from: '2025-01-01',
      date_to: '2026-09-04',
      calendar_version: 'reviewed-v1',
    },
    evidence_update_available: false,
    broker: {
      provider: 'fyers',
      configured: false,
      state: 'ready_to_download',
      message: 'Connect a broker before new acquisition.',
    },
  })
  vi.mocked(scannerResearch.sources).mockResolvedValue([source])
  vi.mocked(scannerResearch.preflight).mockResolvedValue({ planned_evaluations: 1 })
})
describe('Scanner Research journey', () => {
  it('preserves a legacy draft until New run explicitly starts the connector workflow', async () => {
    const draft = freshDraft()
    draft.execution = undefined
    draft.config = {
      ...draft.config,
      target_pct: 17,
      trade_horizon: 'intraday',
      hold_minutes: 30,
      max_exposure_pct: 45,
      modes: ['Zero Only'],
    }
    writeDraft('account', draft)
    const user = userEvent.setup()
    mount()
    expect(screen.getByText('Legacy scanner setup. New run uses VectorBT.')).toBeVisible()
    expect(screen.getByLabelText('Profit target (%)')).toHaveValue(17)
    expect(screen.getByLabelText('Maximum holding minutes')).toHaveValue(30)
    await user.click(screen.getByRole('button', { name: 'New run' }))
    expect(screen.getByText('VectorBT · Daily backtest')).toBeVisible()
    expect(screen.getByLabelText('Profit target (%)')).toHaveValue(10)
    expect(screen.queryByLabelText('Holding period')).not.toBeInTheDocument()
    await screen.findByRole('option', { name: /2 signals/ })
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    expect(scannerResearch.preflight).toHaveBeenCalledWith({
      source_id: 'source-1',
      config,
      kind: 'backtest',
      specification: { execution: { engine: 'vectorbt', optimizer: 'native' } },
    })
    expect(draft.config.hold_minutes).toBe(30)
  })

  it('blocks an unavailable connector before preparation or submission without falling back', async () => {
    const capabilities = await scannerResearch.capabilities()
    vi.mocked(scannerResearch.capabilities).mockResolvedValue({
      ...capabilities,
      connectors: {
        engines: [{ id: 'scanner', name: 'Scanner', available: true }],
        optimizers: [],
      },
    })
    const user = userEvent.setup()
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('VectorBT is unavailable')
    await user.upload(
      screen.getByLabelText('Dated scanner CSV'),
      new File(['Date,Symbol\n2026-01-05,TCS'], 'signals.csv', { type: 'text/csv' })
    )
    expect(screen.getByRole('button', { name: 'Prepare prices' })).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    const review = screen.getByRole('button', { name: 'Review and run' })
    expect(review).toBeDisabled()
    await user.click(review)
    expect(scannerResearch.upload).not.toHaveBeenCalled()
    expect(scannerResearch.preflight).not.toHaveBeenCalled()
    expect(scannerResearch.submit).not.toHaveBeenCalled()
  })

  it('submits the engine and policy versions returned by preflight unchanged', async () => {
    const execution = {
      engine: 'vectorbt',
      optimizer: 'native',
      contract_version: 'reviewed-contract',
      engine_version: '0.28.5',
      adapter_version: 'reviewed-adapter',
    }
    vi.mocked(scannerResearch.preflight).mockResolvedValue({
      planned_evaluations: 1,
      specification: { execution },
    })
    vi.mocked(scannerResearch.submit).mockResolvedValue(saved)
    vi.mocked(scannerResearch.job).mockResolvedValue(saved)
    const user = userEvent.setup()
    mount()
    await screen.findByRole('option', { name: /2 signals/ })
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await user.click(await screen.findByRole('button', { name: 'Run historical setup' }))
    expect(scannerResearch.submit).toHaveBeenCalledWith({
      source_id: 'source-1',
      config,
      kind: 'backtest',
      specification: { execution },
      request_id: expect.any(String),
    })
  })

  it.each([
    'closed',
    'pending',
  ])('keeps all minute marks and focuses only settled trades (%s)', async (status) => {
    const timestamps = [
      '2026-01-20T15:29:00+05:30',
      '2026-01-21T09:15:00+05:30',
      '2026-01-21T09:16:00+05:30',
      '2026-01-21T09:17:00+05:30',
      '2026-09-04T15:29:00+05:30',
    ]
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      result: {
        ...saved.result,
        metric_basis: 'minute_marked',
        equity_curve: timestamps.map((timestamp, index) => ({
          timestamp,
          date: timestamp.slice(0, 10),
          equity: index > 1 ? 99500 : 100000,
        })),
        ledger: [
          {
            symbol: 'TCS',
            quantity: 1,
            status,
            entry_timestamp: timestamps[1],
            exit_timestamp: status === 'closed' ? timestamps[3] : null,
          },
        ],
      },
    })
    mount('/scanner-research?job=saved-123')
    await screen.findByText('Daily marked equity chart')
    const chart = vi.mocked(PortfolioLineChart).mock.calls.at(-1)?.[0]
    expect(chart?.series[0].data.map((point) => point.date)).toEqual(timestamps)
    if (status === 'closed') {
      expect(chart?.initialRange).toEqual({ from: 0, to: 3 })
    } else {
      expect(chart?.initialRange).toBeUndefined()
    }
    expect(scannerResearch.upload).not.toHaveBeenCalled()
    expect(scannerResearch.submit).not.toHaveBeenCalled()
  })
  it('automatically prepares newly needed prices and continues review with the new source', async () => {
    recoverLegacyDraft()
    const minuteSource = {
      ...source,
      id: 'minute-source',
      provenance: {
        ...source.provenance,
        synthetic: false,
        history_source: 'historify',
        interval: '1m',
      },
    }
    const preparation = {
      ...saved,
      id: 'minute-preparation',
      kind: 'acquire' as const,
      result: { ...saved.result, prepared_source: minuteSource },
    }
    vi.mocked(scannerResearch.upload).mockResolvedValue(source)
    vi.mocked(scannerResearch.preflight)
      .mockResolvedValueOnce({ preparation_job: preparation })
      .mockResolvedValue({ planned_evaluations: 1 })
    vi.mocked(scannerResearch.job).mockResolvedValue(preparation)
    vi.mocked(scannerResearch.submit).mockResolvedValue(saved)
    const user = userEvent.setup()
    mount()
    await user.upload(
      screen.getByLabelText('Dated scanner CSV'),
      new File(['Date,Symbol\n2026-01-05,TCS'], 'signals.csv', { type: 'text/csv' })
    )
    await user.click(screen.getByRole('button', { name: 'Prepare prices' }))
    await screen.findByText('Validated 2 signals across 2 symbols')
    await user.selectOptions(screen.getByLabelText('Holding period'), 'intraday')
    expect(screen.getByLabelText('Maximum holding minutes')).toHaveValue(60)
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await waitFor(() => expect(scannerResearch.preflight).toHaveBeenCalledTimes(2))
    expect(scannerResearch.preflight).toHaveBeenLastCalledWith(
      expect.objectContaining({
        source_id: 'minute-source',
        config: expect.objectContaining({ trade_horizon: 'intraday', hold_minutes: 60 }),
      })
    )
    expect(scannerResearch.upload).toHaveBeenCalledTimes(1)
    expect(scannerResearch.submit).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: 'Run historical setup' }))
    expect(scannerResearch.submit).toHaveBeenCalledWith(
      expect.objectContaining({
        source_id: 'minute-source',
        config: expect.objectContaining({ trade_horizon: 'intraday', hold_minutes: 60 }),
      })
    )
  })
  it('uses minute holding search boundaries for an intraday optimizer', async () => {
    recoverLegacyDraft()
    const user = userEvent.setup()
    mount()
    await user.selectOptions(screen.getByLabelText('Holding period'), 'intraday')
    await user.selectOptions(screen.getByLabelText('Run type'), 'optimize')
    expect(screen.getByLabelText('hold_minutes max')).toHaveValue(120)
    expect(screen.queryByLabelText('hold_sessions max')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/candle interval|database/i)).not.toBeInTheDocument()
  })
  it.each([
    [{ provider: 'NSE final CM bhavcopy' }, 'NSE archive · daily'],
    [{ provider: 'fyers' }, 'Fyers · daily'],
    [
      { provider: 'Zerodha via OpenAlgo history, compared to official NSE raw records' },
      'Zerodha · daily',
    ],
    [{ provider: 'OpenAlgo Historify', history_source: 'historify' }, 'OpenAlgo prices · daily'],
    [
      { provider: 'OpenAlgo Historify', history_source: 'historify', interval: '1m' },
      'OpenAlgo prices · 1 minute',
    ],
    [{ provider: 'fixture', synthetic: true }, 'Demo · synthetic daily prices'],
  ])('labels the actual saved price provider and opens only the summary', async (provenance, expected) => {
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      result: { ...saved.result, coverage: { provenance } },
    })
    const user = userEvent.setup()
    mount('/scanner-research?job=saved-123')
    expect(await screen.findByText(String(expected))).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Trades table' })).not.toBeInTheDocument()
    expect(screen.queryByText('Assumptions and limits')).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Settings' }))
    expect(screen.getByText('Starting cash')).toBeVisible()
    expect(screen.queryByText('Shuffle seed')).not.toBeInTheDocument()
    expect(screen.queryByText('Trailing distance')).not.toBeInTheDocument()
    expect(screen.queryByText('Daily marked equity chart')).not.toBeInTheDocument()
  })
  it('does not silently use archives when broker sign-in is needed', async () => {
    vi.mocked(scannerResearch.capabilities).mockResolvedValue({
      public: { available: true },
      evidence_update_available: false,
      broker: {
        provider: 'fyers',
        configured: true,
        connected: false,
        state: 'sign_in_required',
        action_url: '/broker',
        message: 'Sign in to Fyers.',
      },
    })
    const user = userEvent.setup()
    mount()
    expect(await screen.findByRole('link', { name: 'Connect broker' })).toHaveAttribute(
      'href',
      '/broker'
    )
    await user.upload(
      screen.getByLabelText('Dated scanner CSV'),
      new File(['Date,Symbol\n2026-01-05,TCS'], 'signals.csv', { type: 'text/csv' })
    )
    expect(screen.getByRole('button', { name: 'Prepare prices' })).toBeDisabled()
    expect(scannerResearch.upload).not.toHaveBeenCalled()
    await user.click(screen.getByText('Other data sources'))
    await user.selectOptions(screen.getByLabelText('Price source'), 'public')
    expect(screen.getByRole('button', { name: 'Prepare prices' })).toBeEnabled()
  })
  it('paginates all trades in groups of 25 without changing the export', async () => {
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      result: {
        ...saved.result,
        ledger: Array.from({ length: 26 }, (_, index) => ({
          symbol: `STOCK-${index}`,
          status: 'pending',
          signal_date: `2026-01-${String(index + 1).padStart(2, '0')}`,
        })),
      },
    })
    const user = userEvent.setup()
    mount('/scanner-research?job=saved-123')
    await user.click(await screen.findByRole('tab', { name: 'Trades' }))
    expect(screen.getByText('1–25 of 26')).toBeVisible()
    expect(screen.queryByText('STOCK-25')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next', exact: true }))
    expect(screen.getByText('STOCK-25')).toBeVisible()
    expect(screen.getByText('26–26 of 26')).toBeVisible()
    expect(screen.getByRole('region', { name: 'Trades table' })).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('link', { name: 'Export results' })).toHaveAttribute(
      'href',
      '/scanner-research/api/jobs/saved-123/export'
    )
  })
  it('allows stored OpenAlgo prices to be checked without another broker login', async () => {
    vi.mocked(scannerResearch.capabilities).mockResolvedValue({
      public: { available: true },
      history: { configured: true, stored_available: true, can_prepare: true },
      evidence_update_available: false,
      broker: { provider: '', configured: false, connected: false, state: 'sign_in_required' },
    })
    vi.mocked(scannerResearch.upload).mockResolvedValue(source)
    const user = userEvent.setup()
    mount()
    await screen.findByText('OpenAlgo prices · automatic')
    const csv = new File(['Date,Symbol\n2026-01-05,TCS'], 'signals.csv', { type: 'text/csv' })
    await user.upload(screen.getByLabelText('Dated scanner CSV'), csv)
    expect(screen.queryByRole('link', { name: 'Connect broker' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Prepare prices' }))
    expect(scannerResearch.upload).toHaveBeenCalledWith(csv, 'broker', {
      config,
      kind: 'backtest',
      specification: { execution: { engine: 'vectorbt', optimizer: 'native' } },
    })
  })
  it.each([
    ['offline', 'Calculation worker is offline'],
    ['stale', 'Worker heartbeat is stale'],
    ['maintenance', 'Storage maintenance is active'],
  ] as const)('explains worker %s state with an actionable status', async (state, message) => {
    vi.mocked(scannerResearch.health).mockResolvedValue({
      worker_state: state,
      worker_online: false,
      maintenance: state === 'maintenance',
      last_heartbeat: null,
      active_jobs: 0,
    })
    mount()
    expect(await screen.findByText(new RegExp(message))).toBeVisible()
    expect(screen.getByRole('button', { name: 'Refresh worker status' })).toBeEnabled()
  })
  it('keeps healthy status quiet and defaults to broker without source fallback', async () => {
    mount()
    expect(await screen.findByText('OpenAlgo prices · automatic')).toBeVisible()
    expect(screen.getByLabelText('Price source')).toHaveValue('broker')
    expect(screen.queryByRole('button', { name: 'Refresh worker status' })).not.toBeInTheDocument()
    expect(screen.queryByText('Assumptions and limits')).not.toBeInTheDocument()
  })
  it.each([
    'offline',
    'stale',
    'maintenance',
  ] as const)('keeps worker %s notices out of completed review until the user starts new work', async (state) => {
    vi.mocked(scannerResearch.health).mockResolvedValue({
      worker_state: state,
      worker_online: false,
      maintenance: state === 'maintenance',
      last_heartbeat: null,
      active_jobs: 0,
    })
    vi.mocked(scannerResearch.job).mockResolvedValue(saved)
    mount('/scanner-research?job=saved-123')
    await screen.findByRole('tab', { name: 'Summary' })
    expect(screen.queryByRole('button', { name: 'Refresh worker status' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Export results' })).toBeVisible()
    await userEvent.setup().click(screen.getByRole('button', { name: 'New run' }))
    expect(await screen.findByRole('button', { name: 'Refresh worker status' })).toBeVisible()
  })
  it('omits technical run details while showing the error and recovery', async () => {
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      status: 'failed',
      result: undefined,
      error: 'Price data is incomplete',
      previous_attempt_id: 'older-attempt',
      counts: { evaluated: 42 },
    })
    mount('/scanner-research?job=saved-123')
    expect(await screen.findByText('Price data is incomplete')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Retry run' })).toBeVisible()
    expect(screen.queryByText('Run details')).not.toBeInTheDocument()
    expect(screen.queryByText('Previous attempt: older-attempt')).not.toBeInTheDocument()
  })
  it('keeps one token when retrying a terminal preparation into a new linked attempt', async () => {
    const failed = {
      ...saved,
      kind: 'prepare_source' as const,
      status: 'failed',
      resumable: false,
      result: undefined,
    }
    vi.mocked(scannerResearch.job).mockImplementation(async (id) =>
      id === 'saved-123'
        ? failed
        : { ...failed, id, status: 'queued', previous_attempt_id: 'saved-123' }
    )
    vi.mocked(scannerResearch.retry)
      .mockRejectedValueOnce(new Error('Lost retry response'))
      .mockResolvedValue({
        ...failed,
        id: 'attempt-2',
        status: 'queued',
        previous_attempt_id: 'saved-123',
      })
    const user = userEvent.setup()
    mount('/scanner-research?job=saved-123')
    await user.click(await screen.findByRole('button', { name: 'Retry run' }))
    await screen.findByText('Lost retry response')
    await user.click(screen.getByRole('button', { name: 'Retry run' }))
    await screen.findByRole('button', { name: 'Cancel run' })
    expect(vi.mocked(scannerResearch.retry).mock.calls[0]).toEqual(
      vi.mocked(scannerResearch.retry).mock.calls[1]
    )
    expect(scannerResearch.resume).not.toHaveBeenCalled()
  })
  it('turns an unchanged terminal draft into an explicitly linked new attempt', async () => {
    const failed = { ...saved, status: 'failed', result: undefined, resumable: false }
    vi.mocked(scannerResearch.submit).mockResolvedValue(failed)
    vi.mocked(scannerResearch.job).mockImplementation(async (id) =>
      id === 'saved-123'
        ? failed
        : { ...failed, id, status: 'queued', previous_attempt_id: 'saved-123' }
    )
    vi.mocked(scannerResearch.retry).mockResolvedValue({
      ...failed,
      id: 'new-calculation',
      status: 'queued',
      previous_attempt_id: 'saved-123',
    })
    const user = userEvent.setup()
    mount()
    await screen.findByRole('option', { name: /2 signals/ })
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await user.click(await screen.findByRole('button', { name: 'Run historical setup' }))
    await screen.findByText(/After resolving the issue/)
    await user.click(screen.getByRole('button', { name: 'New run' }))
    expect(screen.getByLabelText('Saved signals and prices')).toHaveValue('')
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await user.click(await screen.findByRole('button', { name: 'Retry run' }))
    await screen.findByRole('button', { name: 'Cancel run' })
    expect(scannerResearch.submit).toHaveBeenCalledTimes(1)
    expect(scannerResearch.retry).toHaveBeenCalledWith('saved-123', expect.any(String))
  })
  it('discovers older retained runs through server pagination and meaningful search', async () => {
    vi.mocked(scannerResearch.jobs).mockImplementation(async (params) =>
      params?.cursor === 'after-120'
        ? {
            items: [{ ...saved, id: 'oldest', title: 'January scanner · first retained backtest' }],
            next_cursor: null,
          }
        : {
            items: [{ ...saved, id: 'recent-150', title: 'Recent scanner' }],
            next_cursor: 'after-120',
          }
    )
    vi.mocked(scannerResearch.job).mockResolvedValue({ ...saved, id: 'oldest' })
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: 'Saved runs' }))
    await user.click(await screen.findByRole('button', { name: 'Older results' }))
    await user.click(
      await screen.findByRole('button', { name: /January scanner · first retained backtest/ })
    )
    await screen.findByRole('tab', { name: 'Summary' })
    expect(scannerResearch.job).toHaveBeenCalledWith('oldest')
    await user.click(screen.getByRole('button', { name: 'Saved runs' }))
    await user.type(screen.getByLabelText('Find saved work'), 'January')
    await waitFor(() =>
      expect(scannerResearch.jobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'January', cursor: '' })
      )
    )
    expect(scannerResearch.upload).not.toHaveBeenCalled()
  })
  it.each([
    'fyers',
    'zerodha',
    'dhan',
  ])('uses OpenAlgo automatic history with %s without browser credentials', async (provider) => {
    vi.mocked(scannerResearch.capabilities).mockResolvedValue({
      public: {
        available: true,
        date_from: '2025-01-01',
        date_to: '2026-09-04',
        calendar_years: [2025, 2026],
      },
      evidence_update_available: true,
      broker: { provider, configured: true, connected: true, state: 'ready_to_download' },
    })
    vi.mocked(scannerResearch.upload).mockResolvedValue({
      preparation_job: {
        ...saved,
        id: 'acquire-1',
        kind: 'acquire',
        status: 'queued',
        result: undefined,
      },
    })
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      id: 'acquire-1',
      kind: 'acquire',
      status: 'running',
      progress: 20,
      result: undefined,
    })
    const user = userEvent.setup()
    mount()
    expect(await screen.findByText('OpenAlgo prices · automatic')).toBeVisible()
    const csv = new File(['Date,Symbol\n2026-01-05,TCS'], 'broker.csv', { type: 'text/csv' })
    await user.upload(screen.getByLabelText('Dated scanner CSV'), csv)
    await user.click(screen.getByRole('button', { name: 'Prepare prices' }))
    await screen.findByText(/Preparing prices/)
    expect(scannerResearch.upload).toHaveBeenCalledWith(csv, 'broker', {
      config,
      kind: 'backtest',
      specification: { execution: { engine: 'vectorbt', optimizer: 'native' } },
    })
    expect(screen.queryByLabelText(/password|token/i)).not.toBeInTheDocument()
  })
  it('queues a reviewed official extension and explains source preparation after completion', async () => {
    vi.mocked(scannerResearch.capabilities).mockResolvedValue({
      public: { available: true, date_to: '2026-09-04' },
      evidence_update_available: true,
      broker: { provider: 'fyers', configured: false },
    })
    vi.mocked(scannerResearch.updateEvidence).mockResolvedValue({
      ...saved,
      id: 'update-1',
      kind: 'evidence_update',
      status: 'queued',
      result: undefined,
    })
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      id: 'update-1',
      kind: 'evidence_update',
    })
    const user = userEvent.setup()
    mount()
    await screen.findByRole('option', { name: /2 signals/ })
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    await user.click(screen.getByText('Upload a dated scanner CSV'))
    await user.click(screen.getByText('Other data sources'))
    await user.selectOptions(screen.getByLabelText('Price source'), 'public')
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    await user.click(screen.getByText('Extend reviewed official evidence'))
    await user.type(screen.getByLabelText('Requested official evidence end date'), '2026-09-30')
    await user.click(screen.getByRole('button', { name: 'Queue official evidence update' }))
    await screen.findByText('Official evidence update completed')
    expect(scannerResearch.updateEvidence).toHaveBeenCalledWith(
      'source-1',
      '2026-09-30',
      expect.any(String)
    )
  })
  it('requires validation then submits exact selected configuration and opens saved report', async () => {
    vi.mocked(scannerResearch.upload).mockResolvedValue({
      id: 'source-1',
      receipt: {
        input_rows: 2,
        signal_count: 2,
        duplicates_removed: 0,
        date_from: '2026-01-05',
        date_to: '2026-01-06',
        symbol_count: 2,
        warnings: [],
      },
      coverage: { status: 'ready' },
      provenance: {
        provider: 'synthetic fixture',
        exchange: 'NSE',
        interval: 'D',
        adjustment_basis: 'synthetic',
        calendar_basis: 'weekdays only',
        synthetic: true,
      },
    })
    vi.mocked(scannerResearch.submit).mockResolvedValue(saved)
    vi.mocked(scannerResearch.job).mockResolvedValue(saved)
    const user = userEvent.setup()
    mount()
    expect(screen.getByRole('button', { name: 'Review and run' })).toBeDisabled()
    await user.upload(
      screen.getByLabelText('Dated scanner CSV'),
      new File(['Date,Symbol\n2026-01-05,TCS'], 'signals.csv', { type: 'text/csv' })
    )
    await user.click(screen.getByRole('button', { name: 'Prepare prices' }))
    await screen.findByText('Validated 2 signals across 2 symbols')
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await screen.findByText('Preflight receipt')
    await user.click(screen.getByRole('button', { name: 'Run historical setup' }))
    await screen.findByRole('tab', { name: 'Summary' })
    expect(scannerResearch.submit).toHaveBeenCalledWith({
      source_id: 'source-1',
      config,
      kind: 'backtest',
      specification: { execution: { engine: 'vectorbt', optimizer: 'native' } },
      request_id: expect.any(String),
    })
    expect(screen.getByText('INR 1,00,400', { selector: 'dd' })).toBeVisible()
    expect(screen.queryByText('Missing candle cannot prove no stop touch')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Export results' })).toHaveAttribute(
      'href',
      '/scanner-research/api/jobs/saved-123/export'
    )
  })
  it('reopens exact saved evidence from a URL without uploading or recalculating', async () => {
    vi.mocked(scannerResearch.job).mockResolvedValue(saved)
    mount('/scanner-research?job=saved-123')
    await screen.findByRole('tab', { name: 'Summary' })
    expect(scannerResearch.job).toHaveBeenCalledWith('saved-123')
    expect(scannerResearch.upload).not.toHaveBeenCalled()
    expect(scannerResearch.submit).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Dated scanner CSV')).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'New run' }))
    expect(screen.getByLabelText('Dated scanner CSV')).toBeVisible()
  })
  it('surfaces provenance rejection and keeps submission unavailable', async () => {
    vi.mocked(scannerResearch.upload).mockRejectedValue(
      new Error('Historify adjustment basis is unknown')
    )
    const user = userEvent.setup()
    mount()
    await user.selectOptions(screen.getByLabelText('Price source'), 'historify')
    await user.upload(
      screen.getByLabelText('Dated scanner CSV'),
      new File(['Date,Symbol'], 'signals.csv', { type: 'text/csv' })
    )
    await user.click(screen.getByRole('button', { name: 'Prepare prices' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Historify adjustment basis is unknown'
    )
    expect(screen.getByRole('button', { name: 'Review and run' })).toBeDisabled()
  })
  it('requests cancellation for a persisted queued run', async () => {
    const queued = { ...saved, status: 'queued', progress: 0, result: undefined }
    vi.mocked(scannerResearch.job).mockResolvedValue(queued)
    vi.mocked(scannerResearch.cancel).mockResolvedValue({ ...queued, status: 'cancelled' })
    const user = userEvent.setup()
    mount('/scanner-research?job=saved-123')
    await user.click(await screen.findByRole('button', { name: 'Cancel run' }))
    await waitFor(() => expect(scannerResearch.cancel).toHaveBeenCalledWith('saved-123'))
  })
  it('keeps one request identity after a lost response and retries the exact payload', async () => {
    vi.mocked(scannerResearch.submit)
      .mockRejectedValueOnce(new Error('Response lost'))
      .mockResolvedValue(saved)
    vi.mocked(scannerResearch.job).mockResolvedValue(saved)
    const user = userEvent.setup()
    mount()
    await screen.findByRole('option', { name: /2 signals/ })
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await user.click(await screen.findByRole('button', { name: 'Run historical setup' }))
    await user.click(await screen.findByRole('button', { name: 'Retry same submission' }))
    await screen.findByRole('tab', { name: 'Summary' })
    expect(vi.mocked(scannerResearch.submit).mock.calls[0][0]).toEqual(
      vi.mocked(scannerResearch.submit).mock.calls[1][0]
    )
  })
  it('transfers the selected alternative configuration and original frozen source exactly', async () => {
    const alternative = {
      ...config,
      target_pct: 17,
      entry_priority: 'shuffle' as const,
      priority_seed: 91,
      trailing_enabled: true,
      trailing_pct: 0,
      max_exposure_pct: 45,
      exposure_fill_mode: 'remaining' as const,
      modes: ['Zero Only'],
    }
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      kind: 'optimize',
      result: {
        ...saved.result,
        experiment: {
          kind: 'optimize',
          findings: ['Recorded selection'],
          recommendation_id: 'recommended',
          alternatives: ['alternative'],
          rows: [],
          selected_reports: {
            recommended: saved.result,
            alternative: { ...saved.result, config: alternative },
          },
          neighborhoods: [],
        },
      },
    })
    const user = userEvent.setup()
    mount('/scanner-research?job=saved-123')
    await user.selectOptions(await screen.findByLabelText('Displayed exact setting'), 'alternative')
    await user.click(screen.getByRole('button', { name: 'Backtest this exact setting' }))
    expect(screen.getByLabelText('Profit target (%)')).toHaveValue(17)
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await screen.findByText('Preflight receipt')
    expect(scannerResearch.preflight).toHaveBeenCalledWith({
      source_id: 'source-1',
      config: alternative,
      kind: 'backtest',
      specification: {},
    })
    expect(scannerResearch.submit).not.toHaveBeenCalled()
  })
  it('resumes an interrupted checkpoint as the same job', async () => {
    const interrupted = { ...saved, status: 'interrupted', resumable: true, result: undefined }
    vi.mocked(scannerResearch.job).mockResolvedValue(interrupted)
    vi.mocked(scannerResearch.resume).mockResolvedValue({ ...interrupted, status: 'queued' })
    const user = userEvent.setup()
    mount('/scanner-research?job=saved-123')
    await user.click(await screen.findByRole('button', { name: 'Resume run' }))
    expect(scannerResearch.resume).toHaveBeenCalledWith('saved-123')
    expect(scannerResearch.submit).not.toHaveBeenCalled()
  })
  it('hides stale preflight when settings change and previews later dates before submitting', async () => {
    recoverLegacyDraft()
    const user = userEvent.setup()
    mount()
    await screen.findByRole('option', { name: /2 signals/ })
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await screen.findByText('Preflight receipt')
    await user.selectOptions(screen.getByLabelText('Run type'), 'research')
    expect(screen.queryByText('Preflight receipt')).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('Earlier period cutoff'), '2026-01-05')
    await user.type(screen.getByLabelText('Final later-period cutoff'), '2026-01-30')
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await screen.findByText('Preflight receipt')
    expect(scannerResearch.preflight).toHaveBeenLastCalledWith(
      expect.objectContaining({
        kind: 'research',
        specification: expect.objectContaining({
          intent: 'fixed_setup',
          train_end: '2026-01-05',
          test_end: '2026-01-30',
          prior_explored: true,
        }),
      })
    )
  })
  it('renders later windows as separate reports without inventing an aggregate curve', async () => {
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      kind: 'research',
      result: {
        ...saved.result,
        equity_curve: [],
        ledger: [],
        experiment: {
          kind: 'research',
          exploration: {
            prior_explored: false,
            known_overlap_jobs: ['older-job'],
            interpretation: 'Chronological replay of explored evidence',
          },
          findings: ['Disjoint later windows'],
          folds: [1, 2].map((fold) => ({
            window: {
              fold,
              test_from: `2026-01-${fold === 1 ? '06' : '12'}`,
              test_end: '2026-01-30',
              test_sessions: 14,
            },
            training_ranks: [],
            selected_config: config,
            test_report: {
              ...saved.result,
              summary: { ...saved.result.summary, sample_adequacy: 'Small sample' },
            },
            sensitivities: [],
            finding: 'Small sample',
          })),
        },
      },
    })
    mount('/scanner-research?job=saved-123')
    const user = userEvent.setup()
    await screen.findByLabelText('Later period')
    expect(screen.getAllByText('Daily marked equity chart')).toHaveLength(1)
    expect(screen.getAllByText('Small sample')).toHaveLength(1)
    expect(screen.getByText('Previously explored data')).toBeVisible()
    await user.selectOptions(screen.getByLabelText('Later period'), '1')
    expect(screen.getAllByText('Daily marked equity chart')).toHaveLength(1)
    expect(screen.getByText(/chart includes 1 earlier warmup sessions/)).toBeVisible()
    await user.click(screen.getByText('Previously explored data'))
    expect(screen.getByText(/Chronological replay of explored evidence/)).toBeVisible()
  })
  it('explains ledger outcomes and formats allocation evidence without changing exports', async () => {
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      result: {
        ...saved.result,
        ledger: [
          {
            symbol: 'TCS',
            status: 'closed',
            outcome: 'trailing_stop',
            reason: 'trailing_stop; intraday exit',
            available_exposure: 12345.678,
            entry_raw_price: 125.678,
            exit_raw_price: 128.456,
            funded_budget: 1234.567,
            requested_budget: 2345.678,
          },
        ],
      },
    })
    mount('/scanner-research?job=saved-123')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: 'Trades' }))
    expect(screen.getByText('Trailing stop')).toBeVisible()
    await user.click(screen.getByText('TCS'))
    expect(screen.getByText('Trailing stop reached; exit during the session')).toBeVisible()
    for (const amount of [
      'INR 12,345.68',
      'INR 125.68',
      'INR 128.46',
      'INR 1,234.57',
      'INR 2,345.68',
    ])
      expect(screen.getByText(amount)).toBeVisible()
    expect(screen.getByRole('region', { name: 'Trades table' })).toHaveAttribute('tabindex', '0')
  })
  it('uses a completed asynchronous public preparation as a frozen source', async () => {
    vi.mocked(scannerResearch.upload).mockResolvedValue({
      preparation_job: {
        ...saved,
        id: 'prepare-1',
        kind: 'prepare_source',
        status: 'queued',
        result: undefined,
      },
    })
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      id: 'prepare-1',
      result: { ...saved.result, prepared_source: source },
    })
    const user = userEvent.setup()
    mount()
    await user.upload(
      screen.getByLabelText('Dated scanner CSV'),
      new File(['Date,Symbol\n2026-01-05,TCS'], 'public.csv', { type: 'text/csv' })
    )
    await user.click(screen.getByRole('button', { name: 'Prepare prices' }))
    await screen.findByText('Validated 2 signals across 2 symbols')
    expect(scannerResearch.job).toHaveBeenCalledWith('prepare-1')
    expect(screen.getByRole('button', { name: 'Review and run' })).toBeEnabled()
  })
  it('filters displayed ledger rows without changing the saved export or headline', async () => {
    vi.mocked(scannerResearch.job).mockResolvedValue(saved)
    const user = userEvent.setup()
    mount('/scanner-research?job=saved-123')
    await user.click(await screen.findByRole('tab', { name: 'Trades' }))
    await user.type(await screen.findByLabelText('Search trades'), 'DOES-NOT-MATCH')
    expect(screen.getByText(/0–0 of 0/)).toBeVisible()
    expect(screen.queryByText('Missing candle cannot prove no stop touch')).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Summary' }))
    expect(screen.getByText('INR 1,00,400', { selector: 'dd' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Export results' })).toHaveAttribute(
      'href',
      '/scanner-research/api/jobs/saved-123/export'
    )
  })
  it('prepares a completed search follow-up as distinct work excluding prior indices', async () => {
    const follow = {
      parent_job_id: 'saved-123',
      mode: 'quick',
      axes: {
        target_pct: { min: 5, max: 15, step: 5 },
        stop_pct: { min: 3, max: 7, step: 2 },
        hold_sessions: { min: 3, max: 7, step: 2 },
        trailing_pct: { min: 0, max: 4, step: 2 },
      },
      mode_strategies: [['Bypass']],
      budget: 5,
      rank_by: 'balance',
      include_trailing_off: true,
      exclude_indices: [0, 2, 4],
    }
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      kind: 'optimize',
      result: {
        ...saved.result,
        experiment: {
          kind: 'optimize',
          findings: ['Complete pass'],
          recommendation_id: 'one',
          alternatives: [],
          selected_reports: { one: saved.result },
          follow_up: follow,
        },
      },
    })
    const user = userEvent.setup()
    mount('/scanner-research?job=saved-123')
    await user.click(await screen.findByRole('button', { name: 'Prepare new follow-up' }))
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await screen.findByText('Preflight receipt')
    expect(scannerResearch.preflight).toHaveBeenCalledWith({
      source_id: 'source-1',
      config,
      kind: 'optimize',
      specification: follow,
    })
    expect(scannerResearch.resume).not.toHaveBeenCalled()
    expect(scannerResearch.submit).not.toHaveBeenCalled()
  })
  it('loads an older exact source by ID when it is outside the latest source list', async () => {
    vi.mocked(scannerResearch.sources).mockResolvedValue([])
    vi.mocked(scannerResearch.source).mockResolvedValue(source)
    vi.mocked(scannerResearch.job).mockResolvedValue({
      ...saved,
      kind: 'optimize',
      result: {
        ...saved.result,
        experiment: {
          kind: 'optimize',
          findings: ['Complete'],
          recommendation_id: 'one',
          alternatives: [],
          selected_reports: { one: saved.result },
        },
      },
    })
    const user = userEvent.setup()
    mount('/scanner-research?job=saved-123')
    await user.click(await screen.findByRole('button', { name: 'Backtest this exact setting' }))
    await screen.findByText('Validated 2 signals across 2 symbols')
    expect(scannerResearch.source).toHaveBeenCalledWith('source-1')
    await user.click(screen.getByRole('button', { name: 'Review and run' }))
    await screen.findByText('Preflight receipt')
    expect(scannerResearch.preflight).toHaveBeenCalledWith(
      expect.objectContaining({ source_id: 'source-1', config })
    )
  })
  it('keeps large provenance lazy and bounded while retaining paginated symbol coverage', async () => {
    const receipts = Array.from({ length: 1200 }, (_, index) => ({
      date: `receipt-${index}`,
      hash: `HASH-${index}`,
    }))
    const provenance = {
      ...source.provenance,
      source_receipts: receipts,
      symbol_identities: Object.fromEntries(receipts.map((row, index) => [`SYMBOL-${index}`, row])),
    }
    const largeSource = {
      ...source,
      provenance,
      coverage: {
        status: 'warning',
        warnings: ['Review missing bars'],
        provenance,
        symbols: Array.from({ length: 220 }, (_, index) => ({
          symbol: `EQUITY-${index}`,
          available_bars: 100,
          missing_sessions: [],
        })),
      },
    }
    vi.mocked(scannerResearch.sources).mockResolvedValue([largeSource])
    const user = userEvent.setup()
    const mounted = mount()
    await screen.findByRole('option', { name: /2 signals/ })
    await user.selectOptions(screen.getByLabelText('Saved signals and prices'), 'source-1')
    expect(screen.getByText('Review missing bars')).not.toBeVisible()
    await user.click(screen.getByText('Data notes'))
    expect(screen.getByText('Review missing bars')).toBeVisible()
    expect(screen.queryByText(/HASH-1199/)).not.toBeInTheDocument()
    expect(mounted.container.querySelectorAll('pre')).toHaveLength(0)
    expect(screen.queryByText('Complete source receipt and coverage')).not.toBeInTheDocument()
  })
})
