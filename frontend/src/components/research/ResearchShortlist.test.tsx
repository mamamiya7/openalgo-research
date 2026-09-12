import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError } from 'axios'
import { axe } from 'jest-axe'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { researchCandidates } from '@/api/researchCandidates'
import {
  researchShortlist,
  type ShortlistCandidate,
  type ShortlistDetail,
  type ShortlistPage,
} from '@/api/researchShortlist'
import { researchDefaults } from '@/lib/researchDraft'
import { useAuthStore } from '@/stores/authStore'
import { ResearchShortlist } from './ResearchShortlist'

vi.mock('@/api/researchShortlist', async (original) => ({
  ...(await original<typeof import('@/api/researchShortlist')>()),
  researchShortlist: { list: vi.fn(), get: vi.fn(), update: vi.fn(), remove: vi.fn() },
}))
vi.mock('@/api/researchCandidates', () => ({ researchCandidates: { prepare: vi.fn() } }))
function candidate(overrides: Partial<ShortlistCandidate> = {}): ShortlistCandidate {
  return {
    id: 'candidate',
    experiment_id: 'experiment',
    source_job_id: 'study',
    source_result_artifact: 'frozen-result',
    config_id: 'actual-config',
    period: 'selection',
    origin_kind: 'study',
    trial_number: 1,
    proposal_number: 3,
    is_objective_winner: false,
    name: 'Breakout · Trial 4',
    note: '',
    revision: 1,
    created_at: 1,
    updated_at: 1,
    snapshot: {
      summary: { net_return_pct: 12.123456, max_drawdown_pct: 3, closed_trades: 20 },
      dates: { from: '2026-01-21', to: '2026-07-10' },
      capital: 100000,
      currency: 'INR',
      engine: 'vectorbt',
      objective: { key: 'balanced', score: 9.123456 },
    },
    archived: false,
    source_available: true,
    report: { status: 'available' },
    ...overrides,
  }
}
function detail(value = candidate()): ShortlistDetail {
  return {
    candidate: value,
    archived: value.archived,
    available: true,
    strategies: [
      {
        id: 's',
        name: 'Actual saved strategy',
        allocation_pct: 100,
        config: { ...researchDefaults, target_pct: 7 },
      },
    ],
    analysis: null,
    analysis_catalog: null,
    report: value.report,
  }
}
const page = (items = [candidate()], next_offset: number | null = null): ShortlistPage => ({
  version: 'research-shortlist-v1',
  experiment_id: 'experiment',
  archived: false,
  items,
  total: items.length,
  next_offset,
})
const clients: QueryClient[] = []
function Location() {
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <>
      <output data-testid="url">{location.search}</output>
      <button type="button" onClick={() => navigate(-1)}>
        Browser back
      </button>
    </>
  )
}
function mount({
  url = '/scanner-research?experiment=experiment&view=shortlist',
  readOnly = false,
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  const openReport = vi.fn()
  const tree = (ro: boolean) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <ResearchShortlist experimentId="experiment" readOnly={ro} onOpenReport={openReport} />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const view = render(tree(readOnly))
  return { ...view, client, openReport, change: (ro: boolean) => view.rerender(tree(ro)) }
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { username: 'owner' } })
  vi.mocked(researchShortlist.list).mockResolvedValue(page())
  vi.mocked(researchShortlist.get).mockResolvedValue(detail())
  vi.mocked(researchShortlist.update).mockImplementation(async (_e, _c, patch) =>
    candidate({
      name: patch.name ?? candidate().name,
      note: patch.note ?? '',
      revision: patch.revision + 1,
    })
  )
  vi.mocked(researchShortlist.remove).mockResolvedValue({ removed: true, id: 'candidate' })
  vi.mocked(researchCandidates.prepare).mockResolvedValue({
    config_id: 'actual-config',
    trial_number: 1,
    is_objective_winner: false,
    status: 'queued',
    report_job_id: 'report',
  })
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
  vi.useRealTimers()
})
describe('saved candidate shortlist', () => {
  it('opens pinned settings in place only on demand and returns focus', async () => {
    mount()
    const opener = await screen.findByRole('button', { name: 'Breakout · Trial 4' })
    expect(researchShortlist.get).not.toHaveBeenCalled()
    expect(researchCandidates.prepare).not.toHaveBeenCalled()
    await userEvent.click(opener)
    const dialog = await screen.findByRole('dialog', { name: 'Breakout · Trial 4' })
    expect(within(dialog).getByText('7%')).toBeVisible()
    expect(dialog).toHaveTextContent('Trial 4 · Selection period')
    expect(dialog).not.toHaveTextContent('actual-config')
    expect(screen.getByTestId('url')).toHaveTextContent('shortlist=candidate')
    await act(async () => {
      expect((await axe(document.body)).violations).toEqual([])
    })
    await userEvent.keyboard('{Escape}')
    expect(opener).toHaveFocus()
    expect(researchCandidates.prepare).not.toHaveBeenCalled()
  })
  it('reopens from its URL without session storage and closes to the matching row', async () => {
    const blocked = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw Error('disabled')
    })
    mount({ url: '/scanner-research?experiment=experiment&view=shortlist&shortlist=candidate' })
    expect(await screen.findByRole('dialog', { name: 'Breakout · Trial 4' })).toHaveTextContent(
      'Actual saved strategy'
    )
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Breakout · Trial 4' })).toHaveFocus()
    blocked.mockRestore()
  })
  it('saves name and note using the captured revision, without calculation', async () => {
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Breakout · Trial 4' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Edit name & note' }))
    await userEvent.clear(screen.getByLabelText('Name'))
    await userEvent.type(screen.getByLabelText('Name'), 'Lower drawdown')
    await userEvent.type(screen.getByLabelText('Note'), 'Check concentration')
    vi.mocked(researchShortlist.get).mockResolvedValue(
      detail(candidate({ name: 'Lower drawdown', note: 'Check concentration', revision: 2 }))
    )
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(researchShortlist.update).toHaveBeenCalledWith(
      'experiment',
      'candidate',
      { revision: 1, name: 'Lower drawdown', note: 'Check concentration' },
      expect.any(AbortSignal)
    )
    expect(await screen.findByText('Check concentration')).toBeVisible()
    expect(researchCandidates.prepare).not.toHaveBeenCalled()
  })
  it('preserves unsaved notes on conflict until explicit reload', async () => {
    const error = new AxiosError('Conflict')
    error.response = {
      status: 409,
      data: { code: 'shortlist_revision_conflict', message: 'Changed in another session.' },
    } as typeof error.response
    vi.mocked(researchShortlist.update).mockRejectedValue(error)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Breakout · Trial 4' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Edit name & note' }))
    await userEvent.type(screen.getByLabelText('Note'), 'My unsaved reasoning')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Changed in another session.')
    expect(screen.getByLabelText('Note')).toHaveValue('My unsaved reasoning')
    vi.mocked(researchShortlist.get).mockResolvedValue(
      detail(candidate({ note: 'Saved elsewhere', revision: 4 }))
    )
    await userEvent.click(screen.getByRole('button', { name: 'Reload saved details' }))
    await waitFor(() => expect(screen.getByLabelText('Note')).toHaveValue('Saved elsewhere'))
  })
  it('removes only after confirmation using the observed item revision', async () => {
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Breakout · Trial 4' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Remove', exact: true }))
    expect(researchShortlist.remove).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Keep', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove', exact: true }))
    vi.mocked(researchShortlist.list).mockResolvedValue(page([]))
    await userEvent.click(
      screen.getByRole('button', { name: 'Remove from shortlist', exact: true })
    )
    expect(researchShortlist.remove).toHaveBeenCalledWith(
      'experiment',
      'candidate',
      1,
      expect.any(AbortSignal)
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('Save a trial or backtest to keep it here.')).toBeVisible()
  })
  it('retains exact report access but hides mutations in an archived experiment', async () => {
    vi.mocked(researchShortlist.get).mockResolvedValue(
      detail(candidate({ report: { status: 'ready', report_job_id: 'exact-report' } }))
    )
    const view = mount({ readOnly: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Breakout · Trial 4' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Open report' }))
    expect(view.openReport).toHaveBeenCalledWith(
      'exact-report',
      expect.objectContaining({ source_result_artifact: 'frozen-result', proposal_number: 3 })
    )
    expect(screen.queryByRole('button', { name: 'Edit name & note' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove', exact: true })).not.toBeInTheDocument()
    expect(researchCandidates.prepare).not.toHaveBeenCalled()
  })
  it('prepares the pinned configuration only after an explicit request', async () => {
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Breakout · Trial 4' }))
    await screen.findByRole('button', { name: 'Prepare report' })
    expect(researchCandidates.prepare).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare report' }))
    expect(researchCandidates.prepare).toHaveBeenCalledWith(
      'study',
      'actual-config',
      expect.any(AbortSignal)
    )
  })
  it('paginates without requesting all bookmarks and preserves the offset in the URL', async () => {
    vi.mocked(researchShortlist.list).mockImplementation(async (_e, params) =>
      params?.offset
        ? { ...page([candidate({ id: 'next', name: 'Next candidate' })]), total: 21 }
        : { ...page([candidate()], 20), total: 21 }
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Next', exact: true }))
    expect(await screen.findByRole('button', { name: 'Next candidate', exact: true })).toBeVisible()
    expect(researchShortlist.list).toHaveBeenLastCalledWith(
      'experiment',
      { offset: 20 },
      expect.any(AbortSignal)
    )
    expect(screen.getByTestId('url')).toHaveTextContent('shortlist_offset=20')
  })
  it('keeps unavailable evidence honest and does not substitute settings or start report work', async () => {
    vi.mocked(researchShortlist.get).mockResolvedValue({
      ...detail(),
      available: false,
      error: 'Saved source is missing.',
      strategies: null,
      report: { status: 'unavailable' },
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Breakout · Trial 4' }))
    expect(await screen.findByText('Saved source is missing.')).toBeVisible()
    expect(screen.queryByText('Actual saved strategy')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Prepare report' })).not.toBeInTheDocument()
    expect(researchCandidates.prepare).not.toHaveBeenCalled()
  })
  it('aborts detail requests on close and all outstanding reads on account changes', async () => {
    const reads: AbortSignal[] = []
    vi.mocked(researchShortlist.get).mockImplementation((_e, _i, signal) => {
      reads.push(signal!)
      return new Promise(() => {})
    })
    const view = mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Breakout · Trial 4' }))
    await waitFor(() => expect(reads).toHaveLength(1))
    await userEvent.keyboard('{Escape}')
    expect(reads[0].aborted).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Breakout · Trial 4' }))
    await waitFor(() => expect(reads).toHaveLength(2))
    act(() => useAuthStore.setState({ user: { username: 'other-owner' } }))
    await waitFor(() => expect(reads).toHaveLength(3))
    expect(reads[1].aborted).toBe(true)
    view.unmount()
    expect(reads[2].aborted).toBe(true)
  })
  it('uses capital rather than an empty objective block for a fixed backtest', async () => {
    const fixed = candidate({
      origin_kind: 'backtest',
      trial_number: null,
      proposal_number: null,
      snapshot: { ...candidate().snapshot, objective: null },
    })
    vi.mocked(researchShortlist.get).mockResolvedValue(detail(fixed))
    mount({ url: '/scanner-research?experiment=experiment&view=shortlist&shortlist=candidate' })
    const dialog = await screen.findByRole('dialog', { name: fixed.name })
    expect(within(dialog).getByText('Starting capital')).toBeVisible()
    expect(within(dialog).queryByText('Objective score')).not.toBeInTheDocument()
  })
  it('polls only visible pending reports and stops at terminal state', async () => {
    vi.useFakeTimers()
    vi.mocked(researchShortlist.get)
      .mockResolvedValueOnce(
        detail(candidate({ report: { status: 'running', report_job_id: 'report' } }))
      )
      .mockResolvedValue(
        detail(candidate({ report: { status: 'ready', report_job_id: 'report' } }))
      )
    const view = mount({
      url: '/scanner-research?experiment=experiment&view=shortlist&shortlist=candidate',
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(researchShortlist.get).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(researchShortlist.get).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(researchShortlist.get).toHaveBeenCalledTimes(2)
    expect(researchShortlist.list).toHaveBeenCalledTimes(1)
    view.unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(researchShortlist.get).toHaveBeenCalledTimes(2)
  })
})
