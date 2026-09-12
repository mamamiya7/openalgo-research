import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError } from 'axios'
import type { ComponentProps } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { portfolioResearch } from '@/api/portfolioResearch'
import { type LibraryJob, type ResearchExperiment, researchLibrary } from '@/api/researchLibrary'
import { freshPortfolioDraft } from '@/components/research/PortfolioBuilder'
import type { ResearchShortlist } from '@/components/research/ResearchShortlist'
import type PortfolioResearch from '@/pages/PortfolioResearch'
import { useAuthStore } from '@/stores/authStore'
import ResearchLibrary from './ResearchLibrary'

vi.mock('@/api/researchLibrary', () => ({
  researchLibrary: {
    list: vi.fn(),
    studies: vi.fn(),
    versions: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    saveDraft: vi.fn(),
    run: vi.fn(),
  },
}))
vi.mock('@/api/portfolioResearch', () => ({
  portfolioResearch: { sources: vi.fn(), jobs: vi.fn(), job: vi.fn(), resume: vi.fn() },
}))
vi.mock('@/pages/PortfolioResearch', () => ({
  default: ({ workspace }: ComponentProps<typeof PortfolioResearch>) =>
    workspace && (
      <div>
        <input
          aria-label="Setup title"
          value={workspace.draft.portfolio.name}
          onChange={(event) =>
            workspace.onChange({
              ...workspace.draft,
              portfolio: { ...workspace.draft.portfolio, name: event.target.value },
            })
          }
        />
        <button
          type="button"
          disabled={workspace.disabled}
          onClick={() => void workspace.onRun().then(workspace.onOpenJob)}
        >
          Start calculation
        </button>
      </div>
    ),
}))
vi.mock('@/components/research/ResearchShortlist', () => ({
  ResearchShortlist: ({
    onOpenReport,
    onOpenComparison,
    readOnly,
  }: ComponentProps<typeof ResearchShortlist>) => (
    <div>
      <output>Saved shortlist {readOnly ? 'read only' : 'editable'}</output>
      <button type="button" onClick={() => onOpenComparison?.('saved-comparison')}>
        Compare ready candidates
      </button>
      <button
        type="button"
        onClick={() =>
          onOpenReport('study', {
            id: 'saved-candidate',
            source_job_id: 'study',
            origin_kind: 'study',
          } as never)
        }
      >
        Open saved winner
      </button>
      <button
        type="button"
        onClick={() =>
          onOpenReport('prepared-report', {
            id: 'saved-other',
            source_job_id: 'study',
            origin_kind: 'study',
          } as never)
        }
      >
        Open saved candidate report
      </button>
    </div>
  ),
}))
vi.mock('@/components/research/ResearchComparisons', () => ({
  ResearchComparisons: ({ readOnly }: { readOnly: boolean }) => (
    <output>Saved comparisons {readOnly ? 'read only' : 'editable'}</output>
  ),
}))
vi.mock('@/components/research/ResearchDecisions', () => ({
  ResearchDecisions: ({ readOnly }: { readOnly: boolean }) => (
    <output>Saved decisions {readOnly ? 'read only' : 'editable'}</output>
  ),
}))
const blank = (): ResearchExperiment => ({
  id: 'experiment-1',
  name: 'Breakout research',
  notes: '',
  tags: [],
  pinned: false,
  archived: false,
  revision: 1,
  created_at: 1,
  updated_at: 1,
  job_count: 0,
  version_count: 0,
  draft: freshPortfolioDraft(),
  jobs: [],
  versions: [],
  jobs_next_offset: null,
  versions_next_offset: null,
})
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}
function show(url = '/scanner-research', client?: QueryClient) {
  const cache =
    client ??
    new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[url]}>
        <ResearchLibrary />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
beforeEach(() => {
  vi.resetAllMocks()
  sessionStorage.clear()
  useAuthStore.setState({ user: null })
  vi.mocked(researchLibrary.list).mockResolvedValue({ items: [], next_offset: null })
  vi.mocked(researchLibrary.get).mockResolvedValue(blank())
  vi.mocked(researchLibrary.create).mockResolvedValue(blank())
  vi.mocked(researchLibrary.saveDraft).mockImplementation(async (_id, revision, draft) => ({
    ...blank(),
    revision: revision + 1,
    draft,
  }))
})
afterEach(cleanup)

describe('research library navigation and lost-work boundaries', () => {
  const savedJob = (
    id: string,
    status: string,
    created_at: number | string,
    resumable = false
  ): LibraryJob => ({
    id,
    status,
    created_at,
    resumable,
    progress: status === 'completed' ? 100 : 40,
    kind: 'portfolio_optimize',
    experiment_id: 'experiment-1',
  })
  it.each([
    'failed',
    'interrupted',
    'cancelled',
    'paused',
  ])('offers the newest %s checkpoint ahead of an older result without automatically resuming', async (status) => {
    const data = blank()
    data.jobs = [
      savedJob('older-checkpoint', 'interrupted', 1, true),
      savedJob('result', 'completed', 2),
      savedJob('recover-this', status, 3, true),
    ]
    vi.mocked(researchLibrary.get).mockResolvedValue(data)
    show('/scanner-research?experiment=experiment-1')
    await userEvent.click(await screen.findByRole('button', { name: 'Resume run', exact: true }))
    expect(screen.getByTestId('location')).toHaveTextContent('job=recover-this')
    expect(screen.getByTestId('location')).toHaveTextContent('view=studies')
    expect(portfolioResearch.resume).not.toHaveBeenCalled()
    expect(researchLibrary.run).not.toHaveBeenCalled()
    expect(researchLibrary.saveDraft).not.toHaveBeenCalled()
  })
  it('continues a newer completed result instead of an older checkpoint or an unrecoverable failure', async () => {
    const data = blank()
    data.jobs = [
      savedJob('old-checkpoint', 'failed', '2026-01-01T00:00:00Z', true),
      savedJob('latest-failure', 'failed', '2026-01-03T00:00:00Z'),
      savedJob('latest-result', 'completed', '2026-01-02T00:00:00Z'),
    ]
    vi.mocked(researchLibrary.get).mockResolvedValue(data)
    show('/scanner-research?experiment=experiment-1')
    await userEvent.click(
      await screen.findByRole('button', { name: 'Continue research', exact: true })
    )
    expect(screen.getByTestId('location')).toHaveTextContent('job=latest-result')
    expect(portfolioResearch.resume).not.toHaveBeenCalled()
  })
  it('keeps active work ahead of recovery and does not resume anything when viewing progress', async () => {
    const data = blank()
    const current = savedJob('active-run', 'running', 2)
    data.jobs = [
      savedJob('new-checkpoint', 'failed', 3, true),
      current,
      savedJob('result', 'completed', 1),
    ]
    vi.mocked(researchLibrary.get).mockResolvedValue(data)
    vi.mocked(portfolioResearch.job).mockResolvedValue(current)
    show('/scanner-research?experiment=experiment-1')
    await userEvent.click(await screen.findByRole('button', { name: 'View progress', exact: true }))
    expect(screen.getByTestId('location')).toHaveTextContent('job=active-run')
    expect(portfolioResearch.resume).not.toHaveBeenCalled()
  })
  it.each([
    false,
    true,
  ])('opens a stopped run for review when no result is available (archived: %s)', async (archived) => {
    const data = blank()
    data.archived = archived
    data.jobs = [savedJob('stopped-run', 'failed', 3, archived)]
    vi.mocked(researchLibrary.get).mockResolvedValue(data)
    show('/scanner-research?experiment=experiment-1')
    await userEvent.click(await screen.findByRole('button', { name: 'View run', exact: true }))
    expect(screen.getByTestId('location')).toHaveTextContent('job=stopped-run')
    expect(portfolioResearch.resume).not.toHaveBeenCalled()
  })
  it('opens an incoming Chartink handoff before any stale experiment selection', () => {
    show(
      '/scanner-research?chartink_import=28e5788b-92a1-4e34-877a-8fc902e912fd&experiment=previous'
    )
    expect(screen.getByRole('heading', { name: 'Chartink history' })).toBeVisible()
    expect(screen.getByText('Waiting for your scanner’s history…')).toBeVisible()
    expect(researchLibrary.get).not.toHaveBeenCalled()
    expect(researchLibrary.list).not.toHaveBeenCalled()
  })
  it('refreshes the library after creating an experiment despite the host cache policy', async () => {
    const user = userEvent.setup()
    vi.mocked(researchLibrary.list)
      .mockResolvedValueOnce({ items: [], next_offset: null })
      .mockResolvedValue({ items: [blank()], next_offset: null })
    show()
    await screen.findByText('Start with a research question')
    await user.click(screen.getByRole('button', { name: 'New experiment', exact: true }))
    await user.type(screen.getByLabelText('Research question or name'), 'Breakout research')
    await user.click(screen.getByRole('button', { name: 'Create experiment' }))
    await screen.findByLabelText('Setup title')
    expect(researchLibrary.create).toHaveBeenCalledWith({
      name: 'Breakout research',
      draft: expect.objectContaining({
        portfolio: expect.objectContaining({ name: 'Breakout research' }),
      }),
    })
    await user.click(screen.getByRole('button', { name: 'Research library', exact: true }))
    expect(await screen.findByRole('button', { name: /Breakout research/ })).toBeVisible()
    expect(researchLibrary.list).toHaveBeenCalledTimes(2)
  })

  it('loads a fresh server revision before editing a cached experiment', async () => {
    const user = userEvent.setup()
    const cache = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } })
    cache.setQueryData(['research-experiment', 'account', 'experiment-1'], blank())
    const pending = deferred<ResearchExperiment>()
    vi.mocked(researchLibrary.get).mockReturnValue(pending.promise)
    show('/scanner-research?experiment=experiment-1&view=setup', cache)
    expect(screen.getByText('Opening experiment…')).toBeVisible()
    expect(screen.queryByLabelText('Setup title')).not.toBeInTheDocument()
    const fresh = blank()
    fresh.revision = 3
    fresh.draft.portfolio.name = 'Saved in another browser'
    await act(async () => pending.resolve(fresh))
    expect(await screen.findByLabelText('Setup title')).toHaveValue('Saved in another browser')
    await user.clear(screen.getByLabelText('Setup title'))
    await user.type(screen.getByLabelText('Setup title'), 'New idea')
    await user.click(screen.getByRole('button', { name: 'Research library', exact: true }))
    await screen.findByRole('heading', { name: 'Research library' })
    expect(researchLibrary.saveDraft).toHaveBeenCalledWith(
      'experiment-1',
      3,
      expect.objectContaining({ portfolio: expect.objectContaining({ name: 'New idea' }) })
    )
  })

  it('keeps navigation and duplicate submissions locked until the accepted job is linked', async () => {
    const user = userEvent.setup()
    const pending = deferred<Awaited<ReturnType<typeof researchLibrary.run>>>()
    vi.mocked(researchLibrary.run).mockReturnValue(pending.promise)
    show('/scanner-research?experiment=experiment-1&view=setup')
    await user.click(await screen.findByRole('button', { name: 'Start calculation' }))
    expect(screen.getByRole('button', { name: 'Research library', exact: true })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Setup', exact: true })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Start calculation' })).toBeDisabled()
    const job = { id: 'accepted-job', status: 'queued', kind: 'portfolio_backtest' }
    await act(async () =>
      pending.resolve({ experiment: blank(), job, version: {} } as Awaited<
        ReturnType<typeof researchLibrary.run>
      >)
    )
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('job=accepted-job')
    )
    expect(researchLibrary.run).toHaveBeenCalledTimes(1)
  })

  it('preserves a conflicting local draft as a separate experiment', async () => {
    const user = userEvent.setup()
    const changed = new AxiosError('Conflict', undefined, undefined, undefined, {
      status: 409,
      statusText: 'Conflict',
      data: { message: 'Changed elsewhere' },
      headers: {},
      config: {} as never,
    })
    vi.mocked(researchLibrary.saveDraft).mockRejectedValue(changed)
    const copy = blank()
    copy.id = 'copy-id'
    copy.name = 'Breakout research (copy)'
    copy.draft.portfolio.name = 'My unsaved idea'
    vi.mocked(researchLibrary.create).mockResolvedValue(copy)
    vi.mocked(researchLibrary.get).mockImplementation(async (id) =>
      id === 'copy-id' ? copy : blank()
    )
    show('/scanner-research?experiment=experiment-1&view=setup')
    await user.clear(await screen.findByLabelText('Setup title'))
    await user.type(screen.getByLabelText('Setup title'), 'My unsaved idea')
    await user.click(await screen.findByRole('button', { name: 'Keep my draft as a copy' }))
    await screen.findByRole('heading', { name: 'Breakout research (copy)' })
    expect(screen.getByLabelText('Setup title')).toHaveValue('My unsaved idea')
    expect(researchLibrary.create).toHaveBeenCalledWith(
      expect.objectContaining({
        draft: expect.objectContaining({
          portfolio: expect.objectContaining({ name: 'My unsaved idea' }),
        }),
      })
    )
    expect(sessionStorage.getItem('research-edit:account:experiment-1')).toBeNull()
  })

  it('opens a usable overview for an unknown saved view', async () => {
    show('/scanner-research?experiment=experiment-1&view=unsupported')
    expect(await screen.findByRole('button', { name: 'Add signals' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')
  })
  it('opens decisions inside the experiment and clears old evidence links when changing tabs', async () => {
    show(
      '/scanner-research?experiment=experiment-1&view=decisions&decision=d&decision_event=old&decision_report=selection&return_decision=d'
    )
    expect(await screen.findByText('Saved decisions editable')).toBeVisible()
    expect(
      within(screen.getByRole('navigation', { name: 'Experiment' })).getAllByRole('button')
    ).toHaveLength(5)
    expect(screen.getByRole('button', { name: 'Review', exact: true })).toHaveAttribute(
      'aria-current',
      'page'
    )
    expect(screen.getByRole('button', { name: 'Decisions', exact: true })).toHaveAttribute(
      'aria-current',
      'page'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Comparisons', exact: true }))
    expect(await screen.findByText('Saved comparisons editable')).toBeVisible()
    expect(screen.getByTestId('location')).not.toHaveTextContent('decision_event')
    expect(screen.getByTestId('location')).not.toHaveTextContent('decision_report')
    expect(researchLibrary.run).not.toHaveBeenCalled()
    expect(researchLibrary.saveDraft).not.toHaveBeenCalled()
  })
  it('reveals the active experiment tab horizontally without scrolling the page', async () => {
    const geometry = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function (this: HTMLElement) {
        if (this.getAttribute('aria-label') === 'Experiment')
          return {
            left: 0,
            right: 320,
            top: 200,
            bottom: 240,
            x: 0,
            y: 200,
            width: 320,
            height: 40,
            toJSON: () => ({}),
          }
        if (this.getAttribute('aria-current') === 'page' && this.textContent === 'Review')
          return {
            left: 450,
            right: 550,
            top: 200,
            bottom: 240,
            x: 450,
            y: 200,
            width: 100,
            height: 40,
            toJSON: () => ({}),
          }
        return {
          left: 0,
          right: 0,
          top: 0,
          bottom: 0,
          x: 0,
          y: 0,
          width: 0,
          height: 0,
          toJSON: () => ({}),
        }
      })
    try {
      show('/scanner-research?experiment=experiment-1&view=comparisons')
      await screen.findByText('Saved comparisons editable')
      expect(screen.getByRole('navigation', { name: 'Experiment' }).scrollLeft).toBe(230)
      expect(window.scrollTo).not.toHaveBeenCalled()
    } finally {
      geometry.mockRestore()
    }
  })
  it('opens a saved comparison inside the experiment without a job or setup mutation', async () => {
    show(
      '/scanner-research?experiment=experiment-1&view=shortlist&shortlist_offset=20&compare_candidates=a,b&compare_reference=a'
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Compare ready candidates' }))
    expect(await screen.findByText('Saved comparisons editable')).toBeVisible()
    expect(screen.getByTestId('location')).toHaveTextContent('comparison=saved-comparison')
    expect(screen.getByTestId('location')).toHaveTextContent('compare_reference=a')
    expect(screen.getByTestId('location')).toHaveTextContent('shortlist_offset=20')
    expect(screen.queryByRole('button', { name: 'Start calculation' })).not.toBeInTheDocument()
    expect(researchLibrary.saveDraft).not.toHaveBeenCalled()
    expect(researchLibrary.run).not.toHaveBeenCalled()
  })
  it('opens the saved winner report and returns to the same shortlist detail and page', async () => {
    show(
      '/scanner-research?experiment=experiment-1&view=shortlist&shortlist_offset=20&shortlist=saved-candidate'
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Open saved winner' }))
    expect(screen.getByTestId('location')).toHaveTextContent('job=study')
    expect(screen.getByTestId('location')).toHaveTextContent('report=best')
    expect(screen.getByTestId('location')).toHaveTextContent('return_shortlist=saved-candidate')
    await userEvent.click(screen.getByRole('button', { name: 'Back to shortlist' }))
    expect(screen.getByTestId('location')).toHaveTextContent('shortlist=saved-candidate')
    expect(screen.getByTestId('location')).toHaveTextContent('shortlist_offset=20')
    expect(screen.getByTestId('location')).not.toHaveTextContent('job=')
    expect(researchLibrary.saveDraft).not.toHaveBeenCalled()
    expect(researchLibrary.run).not.toHaveBeenCalled()
  })
  it('opens a prepared report as a backtest while retaining its shortlist origin', async () => {
    show('/scanner-research?experiment=experiment-1&view=shortlist')
    await userEvent.click(
      await screen.findByRole('button', { name: 'Open saved candidate report' })
    )
    expect(screen.getByTestId('location')).toHaveTextContent('job=prepared-report')
    expect(screen.getByTestId('location')).toHaveTextContent('view=backtests')
    expect(screen.getByTestId('location')).toHaveTextContent('return_shortlist=saved-other')
    expect(screen.getByTestId('location')).not.toHaveTextContent('report=best')
    await userEvent.click(screen.getByRole('button', { name: 'Back to shortlist' }))
    expect(screen.getByTestId('location')).toHaveTextContent('shortlist=saved-other')
  })
})
