import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  researchShortlist,
  type ShortlistCandidate,
  type ShortlistPage,
} from '@/api/researchShortlist'
import { useAuthStore } from '@/stores/authStore'
import { SaveToShortlist } from './SaveToShortlist'

vi.mock('@/api/researchShortlist', async (original) => ({
  ...(await original<typeof import('@/api/researchShortlist')>()),
  researchShortlist: { list: vi.fn(), save: vi.fn() },
}))
const page = (saved = false): ShortlistPage => ({
  version: 'research-shortlist-v1',
  experiment_id: 'experiment',
  archived: false,
  items: saved ? [{ id: 'bookmark' } as ShortlistCandidate] : [],
  total: saved ? 1 : 0,
  next_offset: null,
})
const clients: QueryClient[] = []
function mount(readOnly = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  const tree = (ro: boolean, jobId = 'study') => (
    <QueryClientProvider client={client}>
      <SaveToShortlist
        experimentId="experiment"
        jobId={jobId}
        configId="config"
        proposalNumber={0}
        readOnly={ro}
      />
    </QueryClientProvider>
  )
  const view = render(tree(readOnly))
  return {
    ...view,
    client,
    change: (ro = false, jobId = 'study') => view.rerender(tree(ro, jobId)),
  }
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { username: 'owner' } })
  vi.mocked(researchShortlist.list).mockResolvedValue(page())
  vi.mocked(researchShortlist.save).mockResolvedValue({
    candidate: { id: 'bookmark' } as ShortlistCandidate,
    reused: false,
  })
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
})
describe('save an exact candidate', () => {
  it('reads availability only until the explicit click, retaining zero-based proposal identity', async () => {
    mount()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Shortlist', exact: true })).toBeEnabled()
    )
    expect(researchShortlist.save).not.toHaveBeenCalled()
    vi.mocked(researchShortlist.list).mockResolvedValue(page(true))
    await userEvent.click(screen.getByRole('button', { name: 'Shortlist', exact: true }))
    expect(researchShortlist.save).toHaveBeenCalledWith(
      'experiment',
      { job_id: 'study', config_id: 'config', proposal_number: 0 },
      expect.any(AbortSignal)
    )
    expect(await screen.findByRole('button', { name: 'Shortlisted' })).toBeDisabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it('reflects an authoritative deletion after a successful save', async () => {
    const view = mount()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Shortlist', exact: true })).toBeEnabled()
    )
    vi.mocked(researchShortlist.list).mockResolvedValue(page(true))
    await userEvent.click(screen.getByRole('button', { name: 'Shortlist', exact: true }))
    await screen.findByRole('button', { name: 'Shortlisted' })
    vi.mocked(researchShortlist.list).mockResolvedValue(page())
    await act(async () => {
      await view.client.invalidateQueries({
        queryKey: ['research-shortlist', 'owner', 'experiment'],
      })
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Shortlist', exact: true })).toBeEnabled()
    )
  })
  it('keeps archived save disabled and rechecks when archive state changes', async () => {
    const view = mount(true)
    await screen.findByRole('button', { name: 'Shortlist', exact: true })
    expect(screen.getByRole('button', { name: 'Shortlist', exact: true })).toBeDisabled()
    view.change(false)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Shortlist', exact: true })).toBeEnabled()
    )
    expect(researchShortlist.save).not.toHaveBeenCalled()
    expect(researchShortlist.list).toHaveBeenCalledTimes(2)
  })
  it('aborts an unfinished read when the account or source changes', async () => {
    const signals: AbortSignal[] = []
    vi.mocked(researchShortlist.list).mockImplementation((_id, _options, signal) => {
      signals.push(signal!)
      return new Promise(() => {})
    })
    const view = mount()
    await waitFor(() => expect(signals).toHaveLength(1))
    act(() => useAuthStore.setState({ user: { username: 'other' } }))
    await waitFor(() => expect(signals).toHaveLength(2))
    expect(signals[0].aborted).toBe(true)
    view.change(false, 'different-study')
    await waitFor(() => expect(signals).toHaveLength(3))
    expect(signals[1].aborted).toBe(true)
    view.unmount()
    expect(signals[2].aborted).toBe(true)
  })
  it('aborts a save when its candidate leaves the screen', async () => {
    let signal: AbortSignal | undefined
    vi.mocked(researchShortlist.save).mockImplementation((_id, _body, received) => {
      signal = received
      return new Promise(() => {})
    })
    const view = mount()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Shortlist', exact: true })).toBeEnabled()
    )
    await userEvent.click(screen.getByRole('button', { name: 'Shortlist', exact: true }))
    view.unmount()
    expect(signal?.aborted).toBe(true)
  })
})
