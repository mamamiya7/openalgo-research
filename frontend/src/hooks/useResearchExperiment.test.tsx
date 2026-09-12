import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { AxiosError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type ResearchExperiment, researchLibrary } from '@/api/researchLibrary'
import { freshPortfolioDraft } from '@/components/research/PortfolioBuilder'
import { useAuthStore } from '@/stores/authStore'
import { useResearchExperiment } from './useResearchExperiment'

vi.mock('@/api/researchLibrary', () => ({ researchLibrary: { saveDraft: vi.fn(), get: vi.fn() } }))
function experiment(): ResearchExperiment {
  return {
    id: 'research-1',
    name: 'Breakouts',
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
  }
}
function conflict() {
  return new AxiosError('Conflict', undefined, undefined, undefined, {
    status: 409,
    data: { message: 'Changed elsewhere' },
    headers: {},
    statusText: 'Conflict',
    config: {} as never,
  })
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((a, b) => {
    resolve = a
    reject = b
  })
  return { promise, resolve, reject }
}
beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  useAuthStore.setState({ user: null })
  vi.mocked(researchLibrary.saveDraft).mockImplementation(async (_id, revision, draft) => ({
    ...experiment(),
    draft: structuredClone(draft),
    revision: revision + 1,
  }))
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})
describe('durable research draft lifecycle', () => {
  it('refreshes new candidate jobs without changing a dirty draft or its conflict revision', async () => {
    const seed = experiment()
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    act(() => hook.result.current.change({ ...seed.draft, optimizing: true }))
    vi.mocked(researchLibrary.saveDraft).mockRejectedValueOnce(conflict())
    await act(async () => {
      await hook.result.current.flush().catch(() => {})
    })
    const candidate = {
      id: 'candidate-1',
      status: 'queued',
      kind: 'portfolio_backtest',
      progress: 0,
      created_at: 2,
      updated_at: 2,
      experiment_id: seed.id,
      role: 'candidate',
    }
    vi.mocked(researchLibrary.get).mockResolvedValue({
      ...seed,
      revision: 9,
      draft: freshPortfolioDraft(),
      jobs: [candidate],
      job_count: 1,
    })
    await act(async () => {
      await hook.result.current.refreshJobs()
    })
    expect(hook.result.current.server.jobs).toEqual([candidate])
    expect(hook.result.current.server.job_count).toBe(1)
    expect(hook.result.current.server.revision).toBe(1)
    expect(hook.result.current.draft.optimizing).toBe(true)
    expect(hook.result.current.state).toBe('conflict')
    expect(researchLibrary.saveDraft).toHaveBeenCalledTimes(1)
    expect(JSON.parse(sessionStorage.getItem('research-edit:account:research-1')!)).toMatchObject({
      revision: 1,
      draft: { optimizing: true },
    })
  })
  it('retains newer polled status and cancels superseded job refreshes', async () => {
    const seed = experiment()
    const candidate = {
      id: 'candidate-1',
      status: 'queued',
      kind: 'portfolio_backtest',
      progress: 0,
      created_at: 2,
      updated_at: 2,
      experiment_id: seed.id,
      role: 'candidate',
    }
    seed.jobs = [candidate]
    seed.job_count = 1
    const first = deferred<ResearchExperiment>()
    const second = deferred<ResearchExperiment>()
    vi.mocked(researchLibrary.get)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    let old!: Promise<void>
    let latest!: Promise<void>
    act(() => {
      old = hook.result.current.refreshJobs()
      latest = hook.result.current.refreshJobs()
    })
    expect(vi.mocked(researchLibrary.get).mock.calls[0][1]?.aborted).toBe(true)
    act(() =>
      hook.result.current.reflectJob({
        ...candidate,
        status: 'completed',
        progress: 100,
        updated_at: 3,
      })
    )
    await act(async () => {
      second.resolve(seed)
      await latest
    })
    await act(async () => {
      first.resolve({ ...seed, jobs: [] })
      await old
    })
    expect(hook.result.current.server.jobs[0]).toMatchObject({ status: 'completed', updated_at: 3 })
  })
  it('discards old-owner job responses and aborts metadata requests on unmount', async () => {
    const seed = experiment()
    const pending = deferred<ResearchExperiment>()
    vi.mocked(researchLibrary.get).mockReturnValue(pending.promise)
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    let reading!: Promise<void>
    act(() => {
      reading = hook.result.current.refreshJobs()
    })
    useAuthStore.setState({
      user: { username: 'someone-else', broker: null, isLoggedIn: true, loginTime: null },
    })
    await act(async () => {
      pending.resolve({ ...seed, job_count: 99 })
      await reading
    })
    expect(hook.result.current.server.job_count).toBe(0)
    useAuthStore.setState({ user: null })
    const second = deferred<ResearchExperiment>()
    vi.mocked(researchLibrary.get).mockReturnValueOnce(second.promise)
    act(() => {
      reading = hook.result.current.refreshJobs()
    })
    hook.unmount()
    expect(vi.mocked(researchLibrary.get).mock.calls[1][1]?.aborted).toBe(true)
    second.reject(new Error('Disconnected'))
    await expect(reading).resolves.toBeUndefined()
  })
  it('reports metadata failure without discarding the saved library or changing draft state', async () => {
    const seed = experiment()
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    vi.mocked(researchLibrary.get).mockRejectedValueOnce(new Error('Library unavailable'))
    await expect(hook.result.current.refreshJobs()).rejects.toThrow('Library unavailable')
    expect(hook.result.current.server).toEqual(seed)
    expect(hook.result.current.state).toBe('saved')
    expect(hook.result.current.error).toBeNull()
  })
  it('acknowledges server persistence and reopens the accepted version', async () => {
    const seed = experiment()
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    act(() =>
      hook.result.current.change({
        ...seed.draft,
        portfolio: { ...seed.draft.portfolio, name: 'Daily breakouts' },
      })
    )
    expect(hook.result.current.state).toBe('unsaved')
    let saved!: ResearchExperiment
    await act(async () => {
      saved = await hook.result.current.flush()
    })
    expect(saved.revision).toBe(2)
    expect(hook.result.current.state).toBe('saved')
    expect(sessionStorage.getItem('research-edit:account:research-1')).toBeNull()
    hook.unmount()
    const reopened = renderHook(() => useResearchExperiment(saved, 'account'))
    expect(reopened.result.current.draft.portfolio.name).toBe('Daily breakouts')
  })
  it('serializes edits during an outstanding save without replacing newer input', async () => {
    const seed = experiment()
    const first = deferred<ResearchExperiment>()
    vi.mocked(researchLibrary.saveDraft).mockImplementationOnce(() => first.promise)
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    const one = { ...seed.draft, portfolio: { ...seed.draft.portfolio, name: 'First edit' } }
    const two = { ...seed.draft, portfolio: { ...seed.draft.portfolio, name: 'Latest edit' } }
    act(() => hook.result.current.change(one))
    let saving!: Promise<ResearchExperiment>
    act(() => {
      saving = hook.result.current.flush()
    })
    act(() => hook.result.current.change(two))
    await act(async () => {
      first.resolve({ ...seed, revision: 2, draft: one })
      await saving
    })
    expect(researchLibrary.saveDraft).toHaveBeenNthCalledWith(2, seed.id, 2, two)
    expect(hook.result.current.draft.portfolio.name).toBe('Latest edit')
    expect(hook.result.current.server.revision).toBe(3)
    expect(hook.result.current.state).toBe('saved')
  })
  it('retains conflicting input and never silently overwrites another tab', async () => {
    const seed = experiment()
    vi.mocked(researchLibrary.saveDraft).mockRejectedValueOnce(conflict())
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    act(() =>
      hook.result.current.change({
        ...seed.draft,
        portfolio: { ...seed.draft.portfolio, name: 'My change' },
      })
    )
    await act(async () => {
      await hook.result.current.flush().catch(() => {})
    })
    expect(hook.result.current.state).toBe('conflict')
    expect(hook.result.current.draft.portfolio.name).toBe('My change')
    await expect(hook.result.current.flush()).rejects.toThrow('Resolve')
    expect(researchLibrary.saveDraft).toHaveBeenCalledTimes(1)
    vi.mocked(researchLibrary.get).mockResolvedValue({ ...seed, revision: 3 })
    await act(async () => {
      await hook.result.current.reload()
    })
    expect(hook.result.current.state).toBe('saved')
    expect(hook.result.current.server.revision).toBe(3)
  })
  it('saves the latest edit when leaving before the debounce fires', async () => {
    const seed = experiment()
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    act(() => hook.result.current.change({ ...seed.draft, optimizing: true }))
    hook.unmount()
    await waitFor(() =>
      expect(researchLibrary.saveDraft).toHaveBeenCalledWith(
        seed.id,
        1,
        expect.objectContaining({ optimizing: true })
      )
    )
    await waitFor(() =>
      expect(sessionStorage.getItem('research-edit:account:research-1')).toBeNull()
    )
  })
  it('restores unsaved recovery data as a conflict if the server advanced', () => {
    const seed = experiment()
    const local = { ...seed.draft, portfolio: { ...seed.draft.portfolio, name: 'Recovered idea' } }
    sessionStorage.setItem(
      'research-edit:account:research-1',
      JSON.stringify({ revision: 1, draft: local })
    )
    const hook = renderHook(() => useResearchExperiment({ ...seed, revision: 2 }, 'account'))
    expect(hook.result.current.state).toBe('conflict')
    expect(hook.result.current.draft.portfolio.name).toBe('Recovered idea')
    expect(researchLibrary.saveDraft).not.toHaveBeenCalled()
  })
  it('never writes an old account draft after the account changes', async () => {
    const seed = experiment()
    const pending = deferred<ResearchExperiment>()
    vi.mocked(researchLibrary.saveDraft).mockImplementationOnce(() => pending.promise)
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    act(() => hook.result.current.change({ ...seed.draft, optimizing: true }))
    let saving!: Promise<ResearchExperiment>
    act(() => {
      saving = hook.result.current.flush().catch(() => seed)
    })
    useAuthStore.setState({
      user: { username: 'someone-else', broker: null, isLoggedIn: true, loginTime: null },
    })
    hook.unmount()
    pending.reject(new Error('Session changed'))
    await saving
    expect(sessionStorage.getItem('research-edit:account:research-1')).toBeNull()
    expect(researchLibrary.saveDraft).toHaveBeenCalledTimes(1)
  })
  it('shows an unsuccessful save and permits a bounded explicit retry', async () => {
    const seed = experiment()
    vi.mocked(researchLibrary.saveDraft).mockRejectedValueOnce(new Error('Connection unavailable'))
    const hook = renderHook(() => useResearchExperiment(seed, 'account'))
    act(() => hook.result.current.change({ ...seed.draft, equalWeights: false }))
    await act(async () => {
      await hook.result.current.flush().catch(() => {})
    })
    expect(hook.result.current.state).toBe('error')
    expect(hook.result.current.error).toBe('Connection unavailable')
    await act(async () => {
      await hook.result.current.flush()
    })
    expect(hook.result.current.state).toBe('saved')
  })
})
