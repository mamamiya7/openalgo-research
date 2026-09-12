import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation, useSearchParams } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { researchComparisons, type SavedComparison } from '@/api/researchComparisons'
import { researchShortlist, type ShortlistDetail } from '@/api/researchShortlist'
import { ShortlistCompare } from './ShortlistCompare'

vi.mock('@/api/researchComparisons', async (original) => ({
  ...(await original<typeof import('@/api/researchComparisons')>()),
  researchComparisons: { save: vi.fn() },
}))
vi.mock('@/api/researchShortlist', async (original) => ({
  ...(await original<typeof import('@/api/researchShortlist')>()),
  researchShortlist: { get: vi.fn() },
}))
const clients: QueryClient[] = []
function Harness({ open, readOnly }: { open: (id: string) => void; readOnly: boolean }) {
  const [params, setParams] = useSearchParams()
  return (
    <>
      <ShortlistCompare
        key={params.get('compare_candidates')}
        experimentId="e"
        owner="owner"
        readOnly={readOnly}
        onOpen={open}
      />
      <output data-testid="url">{useLocation().search}</output>
      <button
        type="button"
        onClick={() => {
          const next = new URLSearchParams(params)
          next.set('compare_candidates', 'b,c')
          next.delete('compare_request')
          setParams(next)
        }}
      >
        Change selection
      </button>
    </>
  )
}
function mount(url = '/?compare_candidates=a,b', readOnly = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  const open = vi.fn()
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Harness open={open} readOnly={readOnly} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return { ...view, open }
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(researchShortlist.get).mockImplementation(
    async (_e, id) =>
      ({
        candidate: { id, name: `Report ${id}`, report: { status: 'ready' } },
        available: true,
        archived: false,
      }) as ShortlistDetail
  )
  vi.mocked(researchComparisons.save).mockResolvedValue({
    comparison: { id: 'comparison' } as SavedComparison,
    reused: false,
  })
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
})
describe('shortlist comparison selection', () => {
  it('requires an explicit reference, saves only selected identities, and keeps retries stable', async () => {
    vi.mocked(researchComparisons.save).mockRejectedValueOnce(new Error('offline'))
    const { open } = mount()
    await screen.findByRole('option', { name: 'Report a' })
    expect(screen.getByRole('button', { name: 'Compare', exact: true })).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText('Reference'), 'b')
    await userEvent.click(screen.getByRole('button', { name: 'Compare', exact: true }))
    await screen.findByRole('alert')
    const first = vi.mocked(researchComparisons.save).mock.calls[0][1]
    expect(first).toEqual({
      candidate_ids: ['a', 'b'],
      reference_candidate_id: 'b',
      request_id: expect.any(String),
    })
    expect(screen.getByTestId('url')).toHaveTextContent(`compare_request=${first.request_id}`)
    await userEvent.click(screen.getByRole('button', { name: 'Compare', exact: true }))
    await waitFor(() => expect(open).toHaveBeenCalledWith('comparison'))
    expect(vi.mocked(researchComparisons.save).mock.calls[1][1]).toEqual(first)
  })
  it('retries the accepted identity after reload even when its original bookmark is gone', async () => {
    vi.mocked(researchShortlist.get).mockRejectedValue(new Error('not found'))
    const { open } = mount(
      '/?compare_candidates=a,b&compare_reference=a&compare_request=accepted-token'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Compare', exact: true }))
    await waitFor(() => expect(open).toHaveBeenCalledWith('comparison'))
    expect(researchComparisons.save).toHaveBeenCalledWith(
      'e',
      { candidate_ids: ['a', 'b'], reference_candidate_id: 'a', request_id: 'accepted-token' },
      expect.any(AbortSignal)
    )
  })
  it('does not save unprepared or archived candidates and never prepares a report', async () => {
    vi.mocked(researchShortlist.get).mockImplementation(
      async (_e, id) =>
        ({
          candidate: { id, name: id, report: { status: 'available' } },
          available: true,
          archived: false,
        }) as ShortlistDetail
    )
    mount('/?compare_candidates=a,b&compare_reference=a')
    await screen.findByText('Open the candidate and prepare its report before comparing.')
    expect(screen.getByRole('button', { name: 'Compare', exact: true })).toBeDisabled()
    expect(researchComparisons.save).not.toHaveBeenCalled()
  })
  it('aborts a save and ignores a late accepted result after selection changes', async () => {
    let resolve!: (value: Awaited<ReturnType<typeof researchComparisons.save>>) => void
    vi.mocked(researchComparisons.save).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    const { open } = mount('/?compare_candidates=a,b&compare_reference=a')
    await screen.findByRole('option', { name: 'Report a' })
    await userEvent.click(screen.getByRole('button', { name: 'Compare', exact: true }))
    const signal = vi.mocked(researchComparisons.save).mock.calls[0][2]!
    await userEvent.click(screen.getByRole('button', { name: 'Change selection' }))
    expect(signal.aborted).toBe(true)
    await act(async () => {
      resolve({ comparison: { id: 'late' } as SavedComparison, reused: false })
    })
    expect(open).not.toHaveBeenCalled()
    expect(screen.getByTestId('url')).not.toHaveTextContent('compare_request')
  })
  it('keeps the group bounded and disables an archived save even with a retained retry token', async () => {
    mount('/?compare_candidates=a,b,c,d,e,a&compare_reference=a&compare_request=accepted', true)
    await screen.findByRole('option', { name: 'Report d' })
    expect(screen.getByText('4 selected')).toBeVisible()
    expect(researchShortlist.get).toHaveBeenCalledTimes(4)
    expect(screen.getByRole('button', { name: 'Compare', exact: true })).toBeDisabled()
  })
})
