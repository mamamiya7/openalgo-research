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
  it('keeps automatic decisions and retained assumptions collapsed and removes stale decisions on edits', async () => {
    const response = await portfolioResearch.preflight(draft().portfolio)
    vi.mocked(portfolioResearch.preflight).mockResolvedValue({
      ...response,
      automatic_plan: {
        version: 'automatic-search-plan-v1',
        plan_id: 'exact',
        execution_recipe_version: 'automatic-hypotheses-v5',
        summary: { searched_parameters: 3, condition_presets: 92, max_simulations: 320 },
        strategies: [],
        decisions: {
          version: 'automatic-decision-summary-v1',
          automatic: [{ id: 'interval', label: 'Price interval', decision: 'Daily candles' }],
          retained: [
            { id: 'risk', label: 'Account risk', decision: 'Keep original capital and sizing.' },
          ],
          not_searched: [
            { id: 'trailing', label: 'Trailing activation', reason: 'Retains original switch.' },
          ],
          prerequisites: [
            {
              id: 'coverage',
              label: 'Price coverage',
              status: 'pending',
              reason: 'Checked before scoring.',
            },
          ],
          scope: {
            exhaustive: false,
            recommendation_basis: 'Strongest tested scenario.',
            searched_parameters: 3,
          },
        },
      },
    })
    const view = mount()
    const plan = (await screen.findByText('What will be tested')).closest('details')!
    expect(plan.open).toBe(false)
    plan.open = true
    expect(screen.getByText('Daily candles')).toBeVisible()
    expect(screen.getByText('Strongest tested scenario.')).toBeVisible()
    const assumptions = screen.getByText('Assumptions and limits').closest('details')!
    expect(assumptions.open).toBe(false)
    assumptions.open = true
    expect(screen.getByText(/Keep original capital and sizing/)).toBeVisible()
    expect(screen.getByText(/Checked before scoring/)).toBeVisible()
    const changed = draft()
    changed.portfolio.capital = 200000
    view.rerender(view.element(changed))
    expect(screen.queryByText('Daily candles')).not.toBeInTheDocument()
  })
  it('keeps timing reasons collapsed and explains daily strategies sharing minute prices', async () => {
    const response = await portfolioResearch.preflight(draft().portfolio)
    vi.mocked(portfolioResearch.preflight).mockResolvedValue({
      ...response,
      interval: '1m',
      timing_admission: {
        version: 'research-timing-admission-v1',
        id: 'recorded-timing',
        status: 'pending_calendar',
        reason: null,
        required_interval: '1m',
        effective_interval: '1m',
        intent: 'mixed',
        signals: { total: 10, timed: 2, date_only: 8 },
        strategies: [
          {
            id: 'one',
            required_interval: 'D',
            reason: 'Daily execution uses the next recorded session after each dated signal',
            fixed_clocks: { entry_time: null, exit_time: null },
            searched_deadline: 'hold_sessions',
            constraints: [{ parameter: 'hold_sessions', value: 5, maximum: 10 }],
            dormant_parameters: [],
          },
        ],
      },
    })
    const view = mount()
    const explanation = (await screen.findByText('Why these candles?')).closest('details')!
    expect(explanation.open).toBe(false)
    expect(screen.getByText(/Daily execution uses/)).not.toBeVisible()
    explanation.open = true
    expect(screen.getByText(/Minute candles are shared across this portfolio/)).toBeVisible()
    expect(screen.getByText(/checked before downloading/)).toBeVisible()
    const changed = draft()
    changed.portfolio.capital = 200000
    view.rerender(view.element(changed))
    expect(screen.queryByText('Why these candles?')).not.toBeInTheDocument()
  })
  it('shows retained clocks without suggesting they are being optimized', async () => {
    const response = await portfolioResearch.preflight(draft().portfolio)
    vi.mocked(portfolioResearch.preflight).mockResolvedValue({
      ...response,
      interval: '1m',
      timing_admission: {
        version: 'research-timing-admission-v1',
        id: 'clock-timing',
        status: 'pending_calendar',
        reason: null,
        required_interval: '1m',
        effective_interval: '1m',
        intent: 'timed',
        signals: { total: 10, timed: 0, date_only: 10 },
        strategies: [
          {
            id: 'one',
            required_interval: '1m',
            reason: 'Entry/exit clocks require minute prices',
            fixed_clocks: { entry_time: '09:30', exit_time: '15:00' },
            searched_deadline: null,
            constraints: [],
            dormant_parameters: [],
          },
        ],
      },
    })
    mount()
    const explanation = (await screen.findByText('Why these candles?')).closest('details')!
    explanation.open = true
    expect(screen.getByText(/Entry from 09:30.*Exit clock 15:00/)).toBeVisible()
    expect(screen.queryByText(/Minute candles are shared/)).not.toBeInTheDocument()
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
