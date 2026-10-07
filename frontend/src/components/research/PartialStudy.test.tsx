import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError } from 'axios'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type PartialStudyReceipt, researchPartialStudy } from '@/api/researchPartialStudy'
import { useAuthStore } from '@/stores/authStore'
import { PartialStudy } from './PartialStudy'

vi.mock('@/api/researchPartialStudy', () => ({ researchPartialStudy: { get: vi.fn() } }))

function receipt(overrides: Partial<PartialStudyReceipt> = {}): PartialStudyReceipt {
  return {
    version: 'research-partial-study-v1',
    job_id: 'study-job',
    status: 'paused',
    available: true,
    reason: null,
    checkpoint_artifact: 'saved-checkpoint',
    checkpoint_id: 'native-checkpoint',
    scope: 'full',
    automatic: false,
    counts: { proposed: 4, evaluated: 1, reused: 2, rejected: 1 },
    total_rows: 1,
    offset: 0,
    limit: 25,
    next_offset: null,
    analysis_catalog: [],
    rows: [
      {
        config_id: 'config-internal',
        trial_number: 0,
        score: 0,
        summary: { net_return_pct: 0, max_drawdown_pct: 2.5, closed_trades: 0 },
        strategies: [
          {
            id: 'a',
            name: 'Breakout',
            allocation_pct: 100,
            config: {
              target_pct: 4,
              stop_pct: 2,
              hold_sessions: 5,
              order_size_pct: 10,
              trailing_enabled: false,
              trailing_pct: 1,
              cost_bps: 5,
              slippage_bps: 2,
            },
          },
        ],
      },
    ],
    ...overrides,
  }
}
const clients: QueryClient[] = []
function mount(jobId = 'study-job', jobStatus = 'paused') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  const tree = (id: string, status: string) => (
    <QueryClientProvider client={client}>
      <PartialStudy jobId={id} jobStatus={status} />
    </QueryClientProvider>
  )
  const view = render(tree(jobId, jobStatus))
  return { ...view, change: (id: string, status = 'paused') => view.rerender(tree(id, status)) }
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { username: 'saved-trials-owner' } })
  vi.mocked(researchPartialStudy.get).mockResolvedValue(receipt())
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
})

describe('saved partial trials', () => {
  it('retains the compound stock-and-market label when opening an unfinished trial’s settings', async () => {
    const saved = receipt({ automatic: true, scope: 'search' })
    saved.rows[0].strategies[0].entry_condition = {
      version: 'research-joint-entry-conditions-v1',
      hypothesis: {
        id: 'trend_up__trend_up',
        family: 'trend:trend_up',
        label: 'Above SMA 50 · Rising Nifty trend',
        stock_hypothesis: {
          id: 'trend_up',
          family: 'trend',
          label: 'Above SMA 50',
          lookback: 50,
          threshold: null,
        },
        market: { dimension: 'trend', regime: 'up' },
      },
    }
    vi.mocked(researchPartialStudy.get).mockResolvedValue(saved)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'View settings for Trial 1' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Above SMA 50 · Rising Nifty trend')
    expect(screen.queryByRole('button', { name: /backtest|keep|promote/i })).not.toBeInTheDocument()
  })

  it('shows saved zeros and exact settings in place without a winner or execution controls', async () => {
    mount()
    const table = await screen.findByRole('table', { name: 'Saved trials' })
    expect(table).toHaveTextContent('0%')
    expect(table).toHaveTextContent('2.5%')
    expect(screen.getByText('From saved progress · study unfinished')).toBeVisible()
    expect(
      screen.queryByText(/selected|winner|best portfolio|recommended/i)
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /backtest|keep|optimize|resume|promote/i })
    ).not.toBeInTheDocument()
    const opener = within(table).getByRole('button', { name: 'View settings for Trial 1' })
    await userEvent.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'Trial 1 · Saved settings' })
    expect(dialog).toHaveTextContent('Breakout')
    expect(within(dialog).getByText('Profit target').nextElementSibling).toHaveTextContent('4%')
    expect(dialog).not.toHaveTextContent('config-internal')
    await act(async () => expect((await axe(document.body)).violations).toEqual([]))
    await userEvent.keyboard('{Escape}')
    expect(opener).toHaveFocus()
  })

  it('keeps automatic research limited to its saved search period', async () => {
    vi.mocked(researchPartialStudy.get).mockResolvedValue(
      receipt({ automatic: true, scope: 'search' })
    )
    mount()
    expect(await screen.findByText('Automatic research · Search period')).toBeVisible()
    expect(screen.queryByText(/validation|final period|selected/i)).not.toBeInTheDocument()
  })

  it('shows missing metrics as unknown and preserves a saved entry condition', async () => {
    const data = receipt()
    data.rows[0].summary = { net_return_pct: null, max_drawdown_pct: Number.NaN }
    data.rows[0].strategies[0].entry_condition = {
      dimension: 'trend',
      regime: 'up',
      recipe_id: 'pinned-regime-recipe',
    }
    vi.mocked(researchPartialStudy.get).mockResolvedValueOnce(data)
    mount()
    const table = await screen.findByRole('table')
    expect(within(table).getAllByText('—')).toHaveLength(3)
    await userEvent.click(within(table).getByRole('button', { name: 'View settings for Trial 1' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Entry condition: Rising Nifty')
    expect(screen.getByRole('dialog')).not.toHaveTextContent('pinned-regime-recipe')
  })

  it('pins pagination to the same checkpoint and returns to the previous page', async () => {
    vi.mocked(researchPartialStudy.get)
      .mockResolvedValueOnce(receipt({ total_rows: 26, next_offset: 25 }))
      .mockResolvedValueOnce(
        receipt({ total_rows: 26, offset: 25, rows: [{ ...receipt().rows[0], trial_number: 40 }] })
      )
      .mockResolvedValueOnce(receipt({ total_rows: 26, next_offset: 25 }))
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Next' }))
    expect(await screen.findByRole('button', { name: 'View settings for Trial 41' })).toBeVisible()
    expect(researchPartialStudy.get).toHaveBeenLastCalledWith(
      'study-job',
      { offset: 25, expected_checkpoint: 'saved-checkpoint' },
      expect.any(AbortSignal)
    )
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByRole('button', { name: 'View settings for Trial 1' })).toBeVisible()
    expect(researchPartialStudy.get).toHaveBeenLastCalledWith(
      'study-job',
      { offset: 0, expected_checkpoint: 'saved-checkpoint' },
      expect.any(AbortSignal)
    )
  })

  it('reloads after checkpoint replacement without combining different saved pages', async () => {
    const error = new AxiosError('changed')
    error.response = {
      data: { code: 'partial_study_checkpoint_changed' },
      status: 409,
      statusText: '',
      headers: {},
      config: { headers: {} as never },
    }
    vi.mocked(researchPartialStudy.get)
      .mockResolvedValueOnce(receipt({ total_rows: 26, next_offset: 25 }))
      .mockRejectedValueOnce(error)
      .mockResolvedValueOnce(receipt({ checkpoint_artifact: 'new-checkpoint' }))
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Saved progress changed. Load its latest trials.')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Load latest' }))
    expect(await screen.findByRole('table')).toBeVisible()
    expect(researchPartialStudy.get).toHaveBeenLastCalledWith(
      'study-job',
      { offset: 0, expected_checkpoint: undefined },
      expect.any(AbortSignal)
    )
  })

  it('honestly handles old checkpoints and studies with no evaluated portfolios', async () => {
    vi.mocked(researchPartialStudy.get).mockResolvedValueOnce(
      receipt({ available: false, reason: 'No recoverable saved progress was recorded.', rows: [] })
    )
    const view = mount()
    expect(await screen.findByText('No recoverable saved progress was recorded.')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    vi.mocked(researchPartialStudy.get).mockResolvedValueOnce(
      receipt({ job_id: 'empty-study', total_rows: 0, rows: [] })
    )
    view.change('empty-study')
    expect(await screen.findByText('No evaluated portfolios were saved yet.')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('aborts old job and account reads and never renders their late response', async () => {
    let finishOld!: (value: PartialStudyReceipt) => void
    vi.mocked(researchPartialStudy.get).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishOld = resolve
        })
    )
    const view = mount()
    const oldSignal = vi.mocked(researchPartialStudy.get).mock.calls[0][2]!
    vi.mocked(researchPartialStudy.get).mockResolvedValueOnce(
      receipt({ job_id: 'new-study', rows: [] })
    )
    view.change('new-study')
    expect(oldSignal.aborted).toBe(true)
    await act(async () => finishOld(receipt()))
    expect(
      screen.queryByRole('button', { name: 'View settings for Trial 1' })
    ).not.toBeInTheDocument()
    vi.mocked(researchPartialStudy.get).mockImplementationOnce(() => new Promise(() => {}))
    await act(async () => useAuthStore.setState({ user: { username: 'another-owner' } }))
    const accountSignal = vi.mocked(researchPartialStudy.get).mock.calls.at(-1)![2]!
    view.unmount()
    expect(accountSignal.aborted).toBe(true)
  })

  it('discards settings when status changes and makes no reads for a live or completed study', async () => {
    const view = mount()
    await userEvent.click(await screen.findByRole('button', { name: 'View settings for Trial 1' }))
    view.change('study-job', 'running')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    view.change('study-job', 'completed')
    expect(researchPartialStudy.get).toHaveBeenCalledTimes(1)
  })

  it('aborts a pending previous account request before showing any new account trials', async () => {
    let finishOld!: (value: PartialStudyReceipt) => void
    vi.mocked(researchPartialStudy.get).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishOld = resolve
        })
    )
    mount()
    const previousSignal = vi.mocked(researchPartialStudy.get).mock.calls[0][2]!
    vi.mocked(researchPartialStudy.get).mockResolvedValueOnce(receipt({ total_rows: 0, rows: [] }))
    await act(async () => useAuthStore.setState({ user: { username: 'replacement-owner' } }))
    expect(previousSignal.aborted).toBe(true)
    await act(async () => finishOld(receipt()))
    expect(await screen.findByText('No evaluated portfolios were saved yet.')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
