import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { type LiveResearchProgressReceipt, researchLiveProgress } from '@/api/researchLiveProgress'
import { useAuthStore } from '@/stores/authStore'
import { LiveResearchProgress } from './LiveResearchProgress'

vi.mock('@/api/researchLiveProgress', () => ({ researchLiveProgress: { get: vi.fn() } }))

function receipt(
  overrides: Partial<LiveResearchProgressReceipt> = {}
): LiveResearchProgressReceipt {
  return {
    version: 'research-live-progress-v1',
    job_id: 'job-a',
    job_status: 'running',
    available: true,
    provisional: true,
    checkpoint_artifact: 'saved-checkpoint',
    stage: 'joint_refinement',
    fit: {
      key: 'search',
      label: 'Development search',
      dates: { from: '2026-02-25', to: '2026-08-20' },
      completed: 12,
      budget: 68,
    },
    latest: { trial_number: 11, score: 1.4, return_pct: 2.5, drawdown_pct: 1.1, closed_trades: 34 },
    best: { trial_number: 8, score: 2.1, return_pct: 3.2, drawdown_pct: 1.1, closed_trades: 32 },
    baseline: { score: 0, return_pct: 0.5, drawdown_pct: 0.5, closed_trades: 30 },
    events: [
      { kind: 'old', text: 'Older committed fact' },
      { kind: 'reused', text: 'Two recorded proposals reused identical calculations.' },
      { kind: 'objective', text: 'The recorded objective compares return and drawdown.' },
      { kind: 'frozen', text: 'The chosen settings are frozen before later comparisons.' },
    ],
    pace: { seconds_per_trial: 2.4, remaining_search_seconds_upper: null },
    ...overrides,
  }
}
const clients: QueryClient[] = []
type Props = Parameters<typeof LiveResearchProgress>[0]
function mount(initial: Partial<Props> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  const props: Props = {
    jobId: 'job-a',
    jobStatus: 'running',
    recipeVersion: 'automatic-hypotheses-v16',
    ...initial,
  }
  const tree = (next: Props) => (
    <QueryClientProvider client={client}>
      <LiveResearchProgress {...next} />
    </QueryClientProvider>
  )
  const view = render(tree(props))
  return { ...view, change: (next: Partial<Props>) => view.rerender(tree({ ...props, ...next })) }
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { username: 'owner-a' } })
  vi.mocked(researchLiveProgress.get).mockResolvedValue(receipt())
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
  vi.useRealTimers()
})

it('shows recorded same-fit facts and a bounded update list without recommending settings', async () => {
  mount()
  expect(await screen.findByRole('heading', { name: 'Development search' })).toBeVisible()
  expect(screen.getByText('12 of 68 trials saved')).toBeVisible()
  expect(screen.getByText('Provisional development results — not a recommendation.')).toBeVisible()
  const table = screen.getByRole('table', { name: 'Recorded development metrics' })
  const latest = within(table).getByRole('row', { name: /Latest completed/ })
  expect(latest).toHaveTextContent('Trial 12')
  expect(latest).toHaveTextContent('2.5%')
  expect(latest).toHaveTextContent('34')
  expect(within(table).getByRole('row', { name: /Best development score/ })).toHaveTextContent(
    'Trial 9'
  )
  expect(within(table).getByRole('row', { name: 'Original settings 0.5% 0.5% 30 0' })).toBeVisible()
  expect(
    within(screen.getByRole('list', { name: 'Recorded research updates' })).getAllByRole('listitem')
  ).toHaveLength(3)
  expect(screen.queryByText('Older committed fact')).not.toBeInTheDocument()
  expect(screen.getByText('The chosen settings are frozen before later comparisons.')).toBeVisible()
  expect(screen.getByText(/2.4 s per evaluated trial in this fit/)).toBeVisible()
})

it('keeps unavailable metrics as dashes and does not invent timing or decision reasons', async () => {
  vi.mocked(researchLiveProgress.get).mockResolvedValue(
    receipt({
      latest: {
        trial_number: 0,
        score: null,
        return_pct: null,
        drawdown_pct: null,
        closed_trades: null,
      },
      best: null,
      baseline: null,
      events: [],
      pace: { seconds_per_trial: null, remaining_search_seconds_upper: null },
    })
  )
  mount()
  const table = await screen.findByRole('table', { name: 'Recorded development metrics' })
  const cells = within(within(table).getByRole('row', { name: /Latest completed/ })).getAllByRole(
    'cell'
  )
  expect(cells.map((cell) => cell.textContent)).toEqual(['—', '—', '—', '—'])
  expect(
    within(table).queryByRole('rowheader', { name: 'Original settings' })
  ).not.toBeInTheDocument()
  expect(screen.getByText('Timing this fit…')).toBeVisible()
  expect(screen.queryByRole('list', { name: 'Recorded research updates' })).not.toBeInTheDocument()
})

it('aborts account and job requests and ignores responses from a previous identity', async () => {
  let resolveOld!: (value: LiveResearchProgressReceipt) => void
  let firstSignal: AbortSignal | undefined
  vi.mocked(researchLiveProgress.get)
    .mockImplementationOnce((_job, signal) => {
      firstSignal = signal
      return new Promise((resolve) => {
        resolveOld = resolve
      })
    })
    .mockImplementation(async (jobId) =>
      receipt({ job_id: jobId, events: [{ kind: 'active', text: `Current ${jobId}` }] })
    )
  const view = mount()
  await waitFor(() => expect(researchLiveProgress.get).toHaveBeenCalledTimes(1))
  act(() => useAuthStore.setState({ user: { username: 'owner-b' } }))
  expect(await screen.findByText('Current job-a')).toBeVisible()
  expect(firstSignal?.aborted).toBe(true)
  view.change({ jobId: 'job-b' })
  expect(await screen.findByText('Current job-b')).toBeVisible()
  await act(async () =>
    resolveOld(receipt({ events: [{ kind: 'old', text: 'Wrong owner metrics' }] }))
  )
  expect(screen.queryByText('Wrong owner metrics')).not.toBeInTheDocument()
  expect(screen.queryByText('Current job-a')).not.toBeInTheDocument()
})

it('polls every five seconds and stops when the server reports a terminal status', async () => {
  vi.useFakeTimers()
  vi.mocked(researchLiveProgress.get)
    .mockResolvedValueOnce(receipt())
    .mockResolvedValue(receipt({ job_status: 'completed' }))
  mount()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
  expect(researchLiveProgress.get).toHaveBeenCalledTimes(1)
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4999)
  })
  expect(researchLiveProgress.get).toHaveBeenCalledTimes(1)
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1)
  })
  expect(researchLiveProgress.get).toHaveBeenCalledTimes(2)
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000)
  })
  expect(researchLiveProgress.get).toHaveBeenCalledTimes(2)
  expect(screen.queryByRole('region', { name: 'Live research progress' })).not.toBeInTheDocument()
})

it.each(['paused', 'completed'])('reads %s activity once without polling', async (status) => {
  vi.useFakeTimers()
  vi.mocked(researchLiveProgress.get).mockResolvedValue(receipt({ job_status: status }))
  mount({ jobStatus: status, allowCompleted: true })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
  expect(researchLiveProgress.get).toHaveBeenCalledTimes(1)
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000)
  })
  expect(researchLiveProgress.get).toHaveBeenCalledTimes(1)
})

it('does not query unsupported recipes, inactive jobs or unopened completed activity', () => {
  const view = mount({ recipeVersion: 'automatic-hypotheses-v7' })
  view.change({ jobStatus: 'failed' })
  view.change({ jobStatus: 'completed' })
  expect(researchLiveProgress.get).not.toHaveBeenCalled()
  expect(screen.queryByRole('region', { name: 'Live research progress' })).not.toBeInTheDocument()
})
