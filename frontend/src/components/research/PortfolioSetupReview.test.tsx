import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { portfolioResearch } from '@/api/portfolioResearch'
import { researchDefaults } from '@/lib/researchDraft'
import { freshPortfolioDraft } from './PortfolioBuilder'
import { PortfolioSetupReview } from './PortfolioSetupReview'

vi.mock('@/api/portfolioResearch', () => ({ portfolioResearch: { preflight: vi.fn() } }))
const clients: QueryClient[] = []
function draft() {
  const value = freshPortfolioDraft()
  value.portfolio.strategies = [
    {
      id: 'one',
      name: 'Signal CSV',
      type: 'signals',
      source_id: 'source',
      allocation_pct: 100,
      config: { ...researchDefaults },
      search: {},
    },
  ]
  value.portfolio.validation = { mode: 'reserve', train_pct: 80 }
  return value
}
beforeEach(() => {
  vi.mocked(portfolioResearch.preflight).mockImplementation(async (portfolio) => ({
    portfolio,
    interval: 'D',
    versions: {},
    receipt: {
      input_rows: 10,
      signal_count: 10,
      symbol_count: 2,
      strategy_count: 1,
      duplicates_removed: 0,
      warnings: [],
      date_from: '2026-01-05',
      date_to: '2026-06-30',
    },
    period_plan: {
      version: 'research-period-plan-v1',
      mode: 'reserve',
      train_pct: 80,
      selection: { from: '2026-01-05', to: '2026-04-30' },
      evaluation: { from: '2026-05-01', to: '2026-06-30' },
      signal_dates_sha256: 'exact',
      basis: 'signal_dates',
      positions: 'fresh',
    },
  }))
})
afterEach(() => {
  cleanup()
  for (const client of clients) client.clear()
  clients.length = 0
  vi.clearAllMocks()
})
function mount(value = draft()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  const element = (next: ReturnType<typeof draft>) => (
    <QueryClientProvider client={client}>
      <PortfolioSetupReview draft={next} />
    </QueryClientProvider>
  )
  return { ...render(element(value)), element }
}
describe('automatic setup date and interval review', () => {
  it('shows only active searchable settings and hides the old search plan immediately on edits', async () => {
    const response = await portfolioResearch.preflight(draft().portfolio)
    vi.mocked(portfolioResearch.preflight).mockResolvedValue({
      ...response,
      automatic_plan: {
        version: 'automatic-search-plan-v1',
        plan_id: 'exact',
        execution_recipe_version: 'automatic-trade-management-v2',
        summary: { searched_parameters: 3, condition_presets: 0, max_simulations: 118 },
        strategies: [
          {
            id: 'one',
            name: 'Signal CSV',
            parameters: [
              { id: 'stop_pct', label: 'Stop loss', status: 'search', reason: '', current: 2 },
              {
                id: 'order_size_pct',
                label: 'Trade size',
                status: 'fixed',
                reason: '',
                current: 3,
              },
              { id: 'rsi', label: 'RSI', status: 'planned', reason: '', current: null },
            ],
          },
        ],
      },
    })
    const view = mount()
    expect(await screen.findByText('What will be tested')).toBeVisible()
    const details = screen.getByText('What will be tested').closest('details')!
    expect(details.open).toBe(false)
    details.open = true
    expect(screen.getByText(/3 settings · up to 118 calculations/)).toBeVisible()
    expect(screen.getByText('Signal CSV').closest('p')).toHaveTextContent('Signal CSV · Stop loss')
    expect(screen.queryByText('RSI')).not.toBeInTheDocument()
    const changed = draft()
    changed.portfolio.capital = 200000
    view.rerender(view.element(changed))
    expect(screen.queryByText('What will be tested')).not.toBeInTheDocument()
  })
  it('uses the native preview to show signal and actual reserved ranges', async () => {
    mount()
    expect(await screen.findByText(/Daily candles from OpenAlgo/)).toBeVisible()
    expect(screen.getByText(/Selection signals .*Reserved signals/)).toBeVisible()
    expect(portfolioResearch.preflight).toHaveBeenCalledTimes(1)
    expect(vi.mocked(portfolioResearch.preflight).mock.calls[0][1]).toBeInstanceOf(AbortSignal)
  })
  it('hides stale dates immediately on edits and submits only the settled valid settings', async () => {
    const view = mount()
    await screen.findByText(/Daily candles from OpenAlgo/)
    const changed = draft()
    changed.portfolio.date_to = '2026-03-31'
    view.rerender(view.element(changed))
    expect(screen.queryByText(/Daily candles from OpenAlgo/)).not.toBeInTheDocument()
    changed.portfolio.capital = 0
    view.rerender(view.element({ ...changed, portfolio: { ...changed.portfolio } }))
    expect(screen.queryByLabelText('Data and period review')).not.toBeInTheDocument()
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 500))
    })
    expect(portfolioResearch.preflight).toHaveBeenCalledTimes(1)
  })
  it('aborts an outstanding read when setup leaves the screen', async () => {
    vi.mocked(portfolioResearch.preflight).mockImplementation(() => new Promise(() => {}))
    const view = mount()
    await waitFor(() => expect(portfolioResearch.preflight).toHaveBeenCalledTimes(1))
    const signal = vi.mocked(portfolioResearch.preflight).mock.calls[0][1]!
    view.unmount()
    expect(signal.aborted).toBe(true)
  })
})
