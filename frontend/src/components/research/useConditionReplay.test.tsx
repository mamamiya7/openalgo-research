import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { type PortfolioJob, portfolioResearch } from '@/api/portfolioResearch'
import { useAuthStore } from '@/stores/authStore'
import { useConditionReplay } from './useConditionReplay'

vi.mock('@/api/portfolioResearch', () => ({ portfolioResearch: { conditionReplay: vi.fn() } }))
const condition = { strategy_id: 'breakout', dimension: 'trend', regime: 'up' } as const
beforeEach(() => {
  vi.resetAllMocks()
  useAuthStore.setState({ user: null })
})

describe('condition replay lifecycle', () => {
  it('waits for an explicit action and opens the queued report with exact saved context', async () => {
    vi.mocked(portfolioResearch.conditionReplay).mockResolvedValue({
      id: 'filtered',
    } as PortfolioJob)
    const onOpen = vi.fn()
    const { result } = renderHook(() =>
      useConditionReplay('parent', 'experiment-a', 'analysis-a', 'validation', onOpen, false)
    )
    expect(portfolioResearch.conditionReplay).not.toHaveBeenCalled()
    await act(async () => {
      await result.current.test(condition)
    })
    expect(portfolioResearch.conditionReplay).toHaveBeenCalledExactlyOnceWith(
      'parent',
      {
        ...condition,
        experiment_id: 'experiment-a',
        analysis_artifact: 'analysis-a',
        period: 'validation',
        request_id: expect.any(String),
      },
      expect.any(AbortSignal)
    )
    expect(onOpen).toHaveBeenCalledExactlyOnceWith('filtered')
    expect(result.current.busy).toBe(false)
  })

  it('blocks duplicate clicks and retries an uncertain request with the same token', async () => {
    let reject!: (error: Error) => void
    vi.mocked(portfolioResearch.conditionReplay).mockImplementationOnce(
      () =>
        new Promise((_, fail) => {
          reject = fail
        })
    )
    const { result } = renderHook(() =>
      useConditionReplay('parent', 'experiment-a', 'analysis-a', 'selection', vi.fn(), false)
    )
    let pending!: Promise<void>
    act(() => {
      pending = result.current.test(condition)
    })
    await act(async () => {
      await result.current.test(condition)
    })
    expect(portfolioResearch.conditionReplay).toHaveBeenCalledTimes(1)
    expect(result.current.busy).toBe(true)
    const token = vi.mocked(portfolioResearch.conditionReplay).mock.calls[0][1].request_id
    await act(async () => {
      reject(new Error('Connection interrupted'))
      await pending
    })
    expect(result.current.error).toBe('Connection interrupted')
    vi.mocked(portfolioResearch.conditionReplay).mockResolvedValue({
      id: 'filtered',
    } as PortfolioJob)
    await act(async () => {
      await result.current.test(condition)
    })
    expect(vi.mocked(portfolioResearch.conditionReplay).mock.calls[1][1].request_id).toBe(token)
    expect(result.current.error).toBeNull()
  })

  it.each([
    'disabled',
    'missing experiment',
    'missing evidence',
    'missing navigation',
  ] as const)('does not submit with %s', async (reason) => {
    const { result } = renderHook(() =>
      useConditionReplay(
        'parent',
        reason === 'missing experiment' ? undefined : 'experiment-a',
        reason === 'missing evidence' ? null : 'analysis-a',
        'selection',
        reason === 'missing navigation' ? undefined : vi.fn(),
        reason === 'disabled'
      )
    )
    await act(async () => {
      await result.current.test(condition)
    })
    expect(portfolioResearch.conditionReplay).not.toHaveBeenCalled()
  })

  it('aborts when the saved analysis changes and ignores a stale completion', async () => {
    let resolve!: (job: PortfolioJob) => void
    vi.mocked(portfolioResearch.conditionReplay).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    const onOpen = vi.fn()
    const { result, rerender } = renderHook(
      ({ artifact }) =>
        useConditionReplay('parent', 'experiment-a', artifact, 'selection', onOpen, false),
      { initialProps: { artifact: 'analysis-a' } }
    )
    let pending!: Promise<void>
    act(() => {
      pending = result.current.test(condition)
    })
    const signal = vi.mocked(portfolioResearch.conditionReplay).mock.calls[0][2]!
    rerender({ artifact: 'analysis-b' })
    expect(signal.aborted).toBe(true)
    await act(async () => {
      resolve({ id: 'stale' } as PortfolioJob)
      await pending
    })
    expect(onOpen).not.toHaveBeenCalled()
    expect(result.current.busy).toBe(false)
  })

  it('aborts when the experiment changes and starts a new request in the current experiment', async () => {
    let resolve!: (job: PortfolioJob) => void
    vi.mocked(portfolioResearch.conditionReplay).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    const onOpen = vi.fn()
    const { result, rerender } = renderHook(
      ({ experiment }) =>
        useConditionReplay('parent', experiment, 'analysis-a', 'selection', onOpen, false),
      { initialProps: { experiment: 'experiment-a' } }
    )
    let pending!: Promise<void>
    act(() => {
      pending = result.current.test(condition)
    })
    const first = vi.mocked(portfolioResearch.conditionReplay).mock.calls[0]
    rerender({ experiment: 'experiment-b' })
    expect(first[2]!.aborted).toBe(true)
    expect(result.current.busy).toBe(false)
    await act(async () => {
      resolve({ id: 'stale' } as PortfolioJob)
      await pending
    })
    expect(onOpen).not.toHaveBeenCalled()
    vi.mocked(portfolioResearch.conditionReplay).mockResolvedValue({ id: 'current' } as PortfolioJob)
    await act(async () => {
      await result.current.test(condition)
    })
    const current = vi.mocked(portfolioResearch.conditionReplay).mock.calls[1]
    expect(current[1].experiment_id).toBe('experiment-b')
    expect(current[1].request_id).not.toBe(first[1].request_id)
    expect(onOpen).toHaveBeenCalledExactlyOnceWith('current')
  })

  it('aborts on unmount and does not navigate', async () => {
    let resolve!: (job: PortfolioJob) => void
    vi.mocked(portfolioResearch.conditionReplay).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    const onOpen = vi.fn()
    const { result, unmount } = renderHook(() =>
      useConditionReplay('parent', 'experiment-a', 'analysis-a', 'selection', onOpen, false)
    )
    let pending!: Promise<void>
    act(() => {
      pending = result.current.test(condition)
    })
    const signal = vi.mocked(portfolioResearch.conditionReplay).mock.calls[0][2]!
    unmount()
    expect(signal.aborted).toBe(true)
    await act(async () => {
      resolve({ id: 'stale' } as PortfolioJob)
      await pending
    })
    expect(onOpen).not.toHaveBeenCalled()
  })
})
