import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PortfolioJob, PortfolioResult } from '@/api/portfolioResearch'
import { researchTradeChart, type SavedTradeChartPage } from '@/api/researchTradeChart'
import { useAuthStore } from '@/stores/authStore'
import { PortfolioResults } from './PortfolioResults'

vi.mock('@/api/researchTradeChart', () => ({
  researchTradeChart: { get: vi.fn() },
  tradeChartError: () => 'Saved prices could not be opened. Try again.',
}))
vi.mock('./SavedTradeChart', () => ({
  default: ({ page, onPage }: { page: SavedTradeChartPage; onPage: (offset: number) => void }) => (
    <div>
      <output data-testid="saved-chart">
        {page.identity.trade_index}:{page.window.offset}
      </output>
      {page.window.next_offset != null && (
        <button type="button" onClick={() => onPage(page.window.next_offset!)}>
          Later candles
        </button>
      )}
    </div>
  ),
}))
vi.mock('./PortfolioContinuousReport', () => ({ PortfolioContinuousReport: () => null }))
vi.mock('./usePortfolioAnalysis', () => ({
  usePortfolioAnalysis: (_id: string, result: PortfolioResult) => ({
    result,
    busy: false,
    response: { status: 'missing' },
    prepare: vi.fn(),
  }),
}))
vi.mock('./useReportPreferences', () => ({ useReportPreferences: () => ({}) }))

const artifact = 'a'.repeat(64)
function result(): PortfolioResult {
  return {
    config: { initial_capital: 100000 },
    strategies: [],
    per_strategy: [],
    equity_curve: [],
    summary: { initial_capital: 100000, closed_trades: 32 },
    ledger: Array.from({ length: 32 }, (_, index) => ({
      symbol: index < 2 ? 'OTHER' : 'AAA',
      strategy_id: 'a',
      strategy_name: 'Breakout',
      source_row: index,
      status: 'closed',
      entry_date: '2026-01-05',
      exit_date: '2026-01-06',
      entry_price: 100,
      exit_price: 110,
      quantity: 10,
      pnl: 100,
    })),
    report_context: {
      version: 'research-report-context-v1',
      report_id: 'report-a',
      job_id: 'saved-job',
      result_artifact: artifact,
      inputs_artifact: 'b'.repeat(64),
      config_id: null,
      period: 'full',
      period_label: 'Full period',
      dates: { from: '2026-01-05', to: '2026-01-06' },
      analysis_version: null,
      analysis_artifact: null,
    },
  }
}
function page(
  index: number,
  offset = 0,
  period: 'full' | 'evaluation' = 'full'
): SavedTradeChartPage {
  return {
    version: 'research-trade-chart-v1',
    identity: {
      job_id: 'saved-job',
      result_artifact: artifact,
      inputs_artifact: 'b'.repeat(64),
      period,
      trade_index: index,
    },
    symbol: 'AAA',
    exchange: 'NSE',
    interval: 'D',
    timezone: 'Asia/Kolkata',
    candles: [],
    markers: [],
    trade: {},
    window: {
      offset,
      limit: 1500,
      total: 1600,
      next_offset: offset === 0 ? 1500 : null,
      previous_offset: offset ? 0 : null,
      entry_index: 20,
      exit_index: 1579,
    },
  }
}
function mount(value = result()) {
  const job = {
    id: 'saved-job',
    kind: 'portfolio_backtest',
    status: 'completed',
    progress: 100,
    created_at: 1,
    result: value,
  } as PortfolioJob
  return render(
    <PortfolioResults
      job={job}
      result={value}
      onRerun={vi.fn()}
      rerunning={false}
      exportUrl="/export"
    />
  )
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: null })
  vi.mocked(researchTradeChart.get).mockImplementation(async (request, offset) =>
    page(request.tradeIndex, offset, request.period as 'full')
  )
})
afterEach(() => useAuthStore.setState({ user: null }))

describe('saved trades to native chart journey', () => {
  it('fetches on demand and preserves original ledger index through filtering, pagination and return focus', async () => {
    const user = userEvent.setup()
    mount()
    expect(researchTradeChart.get).not.toHaveBeenCalled()
    await user.click(screen.getByRole('tab', { name: 'Trades', exact: true }))
    await user.type(screen.getByRole('textbox', { name: 'Find symbol' }), 'AAA')
    await user.click(screen.getByRole('button', { name: 'Next', exact: true }))
    const opener = screen.getAllByRole('button', { name: 'View AAA trade on chart' })[0]
    await user.click(opener)
    expect(await screen.findByTestId('saved-chart')).toHaveTextContent('27:0')
    expect(researchTradeChart.get).toHaveBeenCalledWith(
      { jobId: 'saved-job', resultArtifact: artifact, period: 'full', tradeIndex: 27 },
      0,
      expect.any(AbortSignal)
    )
    await user.click(screen.getByRole('button', { name: 'Later candles' }))
    expect(await screen.findByTestId('saved-chart')).toHaveTextContent('27:1500')
    await user.click(screen.getByRole('button', { name: 'Close', exact: true }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('textbox', { name: 'Find symbol' })).toHaveValue('AAA')
    expect(screen.getByText('26–30 of 30')).toBeInTheDocument()
    expect(opener).toHaveFocus()
  })

  it('pins later-period chart requests to the later report, not the parent search period', async () => {
    const user = userEvent.setup()
    const value = result()
    const later = result()
    later.report_context = {
      ...later.report_context!,
      report_id: 'report-later',
      period: 'evaluation',
    }
    value.validation = {
      train_from: '2026-01-01',
      train_to: '2026-01-05',
      test_from: '2026-01-06',
      test_to: '2026-01-07',
      training_signals: 1,
      testing_signals: 1,
      label: 'Later',
      selection_basis: 'earlier',
      result: later,
    }
    mount(value)
    await user.click(screen.getByRole('button', { name: 'Later period', exact: true }))
    await user.click(screen.getByRole('tab', { name: 'Trades', exact: true }))
    await user.click(screen.getAllByRole('button', { name: 'View AAA trade on chart' })[0])
    await screen.findByTestId('saved-chart')
    expect(researchTradeChart.get).toHaveBeenCalledWith(
      expect.objectContaining({ period: 'evaluation', tradeIndex: 2 }),
      0,
      expect.any(AbortSignal)
    )
  })

  it('aborts a closed dialog and does not paint its late response', async () => {
    const user = userEvent.setup()
    let resolve!: (value: SavedTradeChartPage) => void
    vi.mocked(researchTradeChart.get).mockReturnValue(
      new Promise((done) => {
        resolve = done
      })
    )
    mount()
    await user.click(screen.getByRole('tab', { name: 'Trades', exact: true }))
    await user.click(screen.getAllByRole('button', { name: 'View AAA trade on chart' })[0])
    const signal = vi.mocked(researchTradeChart.get).mock.calls[0][2]!
    await user.click(screen.getByRole('button', { name: 'Close', exact: true }))
    expect(signal.aborted).toBe(true)
    await act(async () => resolve(page(2)))
    expect(screen.queryByTestId('saved-chart')).not.toBeInTheDocument()
  })

  it('refuses a mismatched response and allows a fresh explicit retry', async () => {
    const user = userEvent.setup()
    vi.mocked(researchTradeChart.get)
      .mockResolvedValueOnce(page(999))
      .mockResolvedValueOnce(page(2))
    mount()
    await user.click(screen.getByRole('tab', { name: 'Trades', exact: true }))
    await user.click(screen.getAllByRole('button', { name: 'View AAA trade on chart' })[0])
    expect(await screen.findByRole('alert')).toHaveTextContent('Saved prices could not be opened')
    expect(screen.queryByTestId('saved-chart')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('saved-chart')).toHaveTextContent('2:0')
  })

  it('discards an open chart and aborts its request when the account changes', async () => {
    const user = userEvent.setup()
    vi.mocked(researchTradeChart.get).mockReturnValue(new Promise(() => {}))
    mount()
    await user.click(screen.getByRole('tab', { name: 'Trades', exact: true }))
    await user.click(screen.getAllByRole('button', { name: 'View AAA trade on chart' })[0])
    const signal = vi.mocked(researchTradeChart.get).mock.calls[0][2]!
    act(() =>
      useAuthStore.setState({
        user: { username: 'other', broker: null, isLoggedIn: true, loginTime: null },
      })
    )
    expect(signal.aborted).toBe(true)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('offers charts for funded pending positions, not skipped or excluded signals', async () => {
    const user = userEvent.setup()
    const value = result()
    value.ledger = [
      { ...value.ledger[2], status: 'pending', symbol: 'OPEN' },
      { ...value.ledger[2], status: 'excluded', symbol: 'EXCLUDED' },
      { ...value.ledger[2], status: 'skipped', symbol: 'SKIPPED', quantity: 0 },
    ]
    mount(value)
    await user.click(screen.getByRole('tab', { name: 'Trades', exact: true }))
    expect(screen.getByRole('button', { name: 'View OPEN trade on chart' })).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'View EXCLUDED trade on chart' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'View SKIPPED trade on chart' })
    ).not.toBeInTheDocument()
  })
})
