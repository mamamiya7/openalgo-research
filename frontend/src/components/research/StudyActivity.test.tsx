import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  researchStudyActivity,
  type StudyActivityReceipt,
  type StudyActivityRow,
} from '@/api/researchStudyActivity'
import { useAuthStore } from '@/stores/authStore'
import { StudyActivity } from './StudyActivity'

vi.mock('@/api/researchStudyActivity', () => ({ researchStudyActivity: { get: vi.fn() } }))

const started = 1789200000
function row(overrides: Partial<StudyActivityRow> = {}): StudyActivityRow {
  return {
    id: 80,
    execution_id: 'attempt-new',
    number: 3,
    config_id: 'internal-config-digest',
    params: { 'strategy-id.target_pct': 3.5, 'strategy-id.hold_sessions': 4 },
    state: 'evaluated',
    value: 0,
    reused: false,
    started_at: started,
    finished_at: started + 12,
    observed_at: started + 12,
    reason_code: null,
    checkpointed: true,
    ...overrides,
  }
}
function receipt(overrides: Partial<StudyActivityReceipt> = {}): StudyActivityReceipt {
  return {
    version: 'research-study-activity-v1',
    job_id: 'study-job',
    job_status: 'completed',
    available: true,
    reason: null,
    executions: [
      {
        id: 'attempt-new',
        started_at: started,
        finished_at: started + 20,
        observed_at: started + 20,
        state: 'completed',
        reason_code: null,
        proposal_budget: 500,
        replayed: 75,
      },
    ],
    executions_truncated: false,
    counts: {
      recorded: 1,
      running: 0,
      evaluated: 1,
      reused: 0,
      allocation_rejected: 0,
      failed: 0,
      cancelled: 0,
      interrupted: 0,
    },
    rows: [row()],
    next_before: null,
    ...overrides,
  }
}
const clients: QueryClient[] = []
function mount(props: { jobId?: string; jobStatus?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  const tree = (jobId = 'study-job', jobStatus = 'completed') => (
    <QueryClientProvider client={client}>
      <StudyActivity
        jobId={jobId}
        jobStatus={jobStatus}
        strategies={[{ id: 'strategy-id', name: 'Breakout' }]}
      />
    </QueryClientProvider>
  )
  const view = render(tree(props.jobId, props.jobStatus))
  return {
    ...view,
    change: (jobId: string, status = 'completed') => view.rerender(tree(jobId, status)),
  }
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { username: 'activity-owner' } })
  vi.mocked(researchStudyActivity.get).mockResolvedValue(receipt())
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
  vi.useRealTimers()
})

describe('durable study activity', () => {
  it('opens exact proposed settings in place with readable labels and returns focus', async () => {
    mount()
    const opener = await screen.findByRole('button', { name: 'View settings for Trial 4' })
    await userEvent.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'Trial 4 · Proposed settings' })
    expect(within(dialog).getByText('Profit target')).toBeVisible()
    expect(within(dialog).getByText('3.5%')).toBeVisible()
    expect(within(dialog).getByText('Holding sessions')).toBeVisible()
    expect(within(dialog).getByText('12 s')).toBeVisible()
    expect(dialog).not.toHaveTextContent('internal-config-digest')
    expect(dialog).not.toHaveTextContent('strategy-id')
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(1)
    await act(async () => {
      expect((await axe(document.body)).violations).toEqual([])
    })
    await userEvent.keyboard('{Escape}')
    expect(opener).toHaveFocus()
  })

  it('preserves unknown completion and score while showing actual observation time', async () => {
    const data = receipt({
      rows: [
        row({
          state: 'interrupted',
          value: null,
          finished_at: null,
          observed_at: started + 200,
          reason_code: 'worker_lost',
          checkpointed: false,
        }),
      ],
    })
    data.executions[0] = {
      ...data.executions[0],
      state: 'interrupted',
      finished_at: null,
      observed_at: started + 200,
      reason_code: 'worker_lost',
    }
    vi.mocked(researchStudyActivity.get).mockResolvedValue(data)
    mount()
    const table = await screen.findByRole('table', { name: 'Proposal activity' })
    const cells = within(table).getAllByRole('cell')
    expect(cells[2]).toHaveTextContent('—')
    expect(cells[3]).toHaveTextContent('—')
    await userEvent.click(within(table).getByRole('button', { name: 'View settings for Trial 4' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Finished').nextElementSibling).toHaveTextContent(
      'Not recorded'
    )
    expect(within(dialog).getByText('Duration').nextElementSibling).toHaveTextContent('—')
    expect(within(dialog).getByText('Objective score').nextElementSibling).toHaveTextContent('—')
    expect(within(dialog).getByText('Last observed').nextElementSibling).not.toHaveTextContent(
      'Not recorded'
    )
    expect(dialog).toHaveTextContent('Its end time was not observed')
    expect(dialog).toHaveTextContent('last recoverable checkpoint may precede it')
    expect(dialog).not.toHaveTextContent('200 s')
  })

  it('keeps a real zero score and distinguishes observed finish from checkpoint coverage', async () => {
    vi.mocked(researchStudyActivity.get).mockResolvedValue(
      receipt({ rows: [row({ checkpointed: false })] })
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'View settings for Trial 4' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Objective score').nextElementSibling).toHaveTextContent(/^0$/)
    expect(within(dialog).getByText('Outcome').nextElementSibling).toHaveTextContent('Finished')
    expect(dialog).toHaveTextContent('last recoverable checkpoint may precede it')
  })

  it('shows honest old-history absence without fabricating a failed trial or controls', async () => {
    vi.mocked(researchStudyActivity.get).mockResolvedValue(
      receipt({
        job_status: 'failed',
        available: false,
        reason: 'not_recorded',
        rows: [],
        executions: [],
      })
    )
    mount({ jobStatus: 'failed' })
    expect(
      await screen.findByText('Detailed activity was not recorded for this study.')
    ).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByText('Failed')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /resume|pause/i })).not.toBeInTheDocument()
  })

  it('waits honestly for the search to start when active activity is not recorded yet', async () => {
    vi.mocked(researchStudyActivity.get).mockResolvedValue(
      receipt({
        job_status: 'running',
        available: false,
        reason: 'not_recorded',
        rows: [],
        executions: [],
      })
    )
    mount({ jobStatus: 'running' })
    expect(
      await screen.findByText('Trial activity will appear when optimization starts.')
    ).toBeVisible()
    expect(
      screen.queryByText('Detailed activity was not recorded for this study.')
    ).not.toBeInTheDocument()
  })

  it('retains exact score and proposed settings precision in the dialog', async () => {
    vi.mocked(researchStudyActivity.get).mockResolvedValue(
      receipt({
        rows: [
          row({
            value: 1.23456789123456,
            params: { 'strategy-id.target_pct': 0.00001 },
            finished_at: started + 12.345,
          }),
        ],
      })
    )
    mount()
    const table = await screen.findByRole('table')
    expect(within(table).getByText('12.3 s')).toBeVisible()
    await userEvent.click(within(table).getByRole('button', { name: 'View settings for Trial 4' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('1.23456789123456')
    expect(dialog).toHaveTextContent('0.00001%')
  })

  it('shows a failed attempt without inventing any proposal and avoids budget progress', async () => {
    const data = receipt({ rows: [], counts: { ...receipt().counts, recorded: 0, evaluated: 0 } })
    data.executions[0] = {
      ...data.executions[0],
      state: 'failed',
      reason_code: 'calculation_failed',
    }
    vi.mocked(researchStudyActivity.get).mockResolvedValue(data)
    mount({ jobStatus: 'failed' })
    expect(await screen.findByText('No proposals recorded yet.')).toBeVisible()
    expect(screen.getByText(/Latest attempt · Failed/)).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByText(/500|75/)).not.toBeInTheDocument()
  })

  it('uses stable before cursors for 25-row pages and can return to latest observations', async () => {
    const first = receipt({
      rows: Array.from({ length: 25 }, (_, index) =>
        row({
          id: 100 - index,
          number: 99 - index,
        })
      ),
      next_before: 76,
    })
    vi.mocked(researchStudyActivity.get).mockImplementation(async (_id, options) =>
      options?.before ? receipt({ rows: [row({ id: 75, number: 74 })] }) : first
    )
    mount()
    expect(within(await screen.findByRole('table')).getAllByRole('row')).toHaveLength(26)
    await userEvent.click(screen.getByRole('button', { name: 'Older', exact: true }))
    expect(await screen.findByRole('rowheader', { name: 'Trial 75' })).toBeVisible()
    expect(researchStudyActivity.get).toHaveBeenLastCalledWith(
      'study-job',
      { before: 76, execution: undefined },
      expect.any(AbortSignal)
    )
    expect(screen.getByRole('button', { name: 'Older', exact: true })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Latest', exact: true }))
    expect(await screen.findByRole('rowheader', { name: 'Trial 100' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Latest', exact: true })).toBeDisabled()
  })

  it('filters actual executions and resets the page without displaying internal IDs', async () => {
    const data = receipt({ next_before: 80, executions_truncated: true })
    data.executions.push({ ...data.executions[0], id: 'attempt-old', started_at: started - 1000 })
    vi.mocked(researchStudyActivity.get).mockResolvedValue(data)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Older', exact: true }))
    const select = await screen.findByRole('combobox', { name: 'Calculation attempt' })
    await userEvent.selectOptions(select, 'attempt-old')
    expect(await screen.findByText('1 proposal observations in this attempt')).toBeVisible()
    expect(researchStudyActivity.get).toHaveBeenLastCalledWith(
      'study-job',
      { before: undefined, execution: 'attempt-old' },
      expect.any(AbortSignal)
    )
    expect(screen.getByRole('region', { name: 'Recorded study activity' })).not.toHaveTextContent(
      'attempt-old'
    )
    expect(screen.getByText(/All attempts includes older recorded activity/)).toBeVisible()
  })

  it('aborts pending requests on account change, job change and unmount', async () => {
    vi.mocked(researchStudyActivity.get).mockImplementation(() => new Promise(() => {}))
    const view = mount()
    const first = vi.mocked(researchStudyActivity.get).mock.calls[0][2]!
    act(() => useAuthStore.setState({ user: { username: 'other-owner' } }))
    expect(first.aborted).toBe(true)
    const second = vi.mocked(researchStudyActivity.get).mock.calls[1][2]!
    view.change('different-job')
    expect(second.aborted).toBe(true)
    const third = vi.mocked(researchStudyActivity.get).mock.calls[2][2]!
    view.unmount()
    expect(third.aborted).toBe(true)
  })

  it('polls only an active visible study and stops as soon as the endpoint becomes terminal', async () => {
    vi.useFakeTimers()
    vi.mocked(researchStudyActivity.get)
      .mockResolvedValueOnce(receipt({ job_status: 'running' }))
      .mockResolvedValue(receipt({ job_status: 'failed' }))
    const view = mount({ jobStatus: 'running' })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(2)
    view.unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(2)
  })

  it('does not poll completed studies and starts a fresh request when run status changes', async () => {
    vi.useFakeTimers()
    const view = mount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(1)
    vi.mocked(researchStudyActivity.get).mockResolvedValue(receipt({ job_status: 'running' }))
    view.change('study-job', 'running')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(3)
  })
})
