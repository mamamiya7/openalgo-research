import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type PortfolioResult, portfolioResearch } from '@/api/portfolioResearch'
import { useAuthStore } from '@/stores/authStore'
import { niftyBenchmark } from './BenchmarkComparison'
import { usePortfolioAnalysis } from './usePortfolioAnalysis'

vi.mock('@/api/portfolioResearch', () => ({
  portfolioResearch: { analysis: vi.fn(), prepareAnalysis: vi.fn() },
}))
const result: PortfolioResult = {
  config: { initial_capital: 1 },
  strategies: [],
  summary: {},
  equity_curve: [],
  ledger: [],
  per_strategy: [],
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  useAuthStore.setState({ user: null })
})
afterEach(() => {
  vi.useRealTimers()
})

describe('saved analysis lifecycle', () => {
  it('prepares market conditions only explicitly and retries the original flag and inputs after failure', async () => {
    vi.mocked(portfolioResearch.analysis).mockResolvedValue({ status: 'missing' })
    vi.mocked(portfolioResearch.prepareAnalysis)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ status: 'queued', market_conditions: true })
    const { result: state } = renderHook(() => usePortfolioAnalysis('job', result, true, true))
    await act(async () => {})
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
    await act(async () => {
      await state.current.prepare(['a.stop_pct'], 'AAA', undefined, true)
    })
    expect(state.current.response.requested_market_conditions).toBe(true)
    expect(state.current.response.requested_benchmark).toBeUndefined()
    expect(state.current.result).toBe(result)
    await act(async () => {
      await state.current.prepare()
    })
    expect(vi.mocked(portfolioResearch.prepareAnalysis).mock.calls).toEqual([
      ['job', ['a.stop_pct'], 'AAA', 'validation', undefined, true],
      ['job', ['a.stop_pct'], 'AAA', 'validation', undefined, true],
    ])
  })

  it('restores market condition progress and exact benchmark/condition retry after refresh', async () => {
    vi.mocked(portfolioResearch.analysis)
      .mockResolvedValueOnce({
        status: 'running',
        benchmark: niftyBenchmark,
        market_conditions: true,
      })
      .mockRejectedValueOnce(new Error('Connection lost'))
    vi.mocked(portfolioResearch.prepareAnalysis).mockResolvedValue({
      status: 'queued',
      market_conditions: true,
    })
    const { result: state } = renderHook(() => usePortfolioAnalysis('job', result, true, false))
    await act(async () => {})
    expect(state.current.response.requested_market_conditions).toBe(true)
    expect(state.current.response.requested_benchmark).toBeUndefined()
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(state.current.response.status).toBe('failed')
    expect(state.current.response.requested_market_conditions).toBe(true)
    await act(async () => {
      await state.current.prepare()
    })
    expect(portfolioResearch.prepareAnalysis).toHaveBeenCalledExactlyOnceWith(
      'job',
      undefined,
      undefined,
      'selection',
      niftyBenchmark,
      true
    )
  })

  it('ignores late condition receipts and old prepare closures after the report period changes', async () => {
    vi.mocked(portfolioResearch.analysis).mockResolvedValue({ status: 'missing' })
    let finish!: (value: Awaited<ReturnType<typeof portfolioResearch.prepareAnalysis>>) => void
    vi.mocked(portfolioResearch.prepareAnalysis).mockImplementation(
      () =>
        new Promise((done) => {
          finish = done
        })
    )
    const { result: state, rerender } = renderHook(
      ({ later }) => usePortfolioAnalysis('job', result, true, later),
      { initialProps: { later: false } }
    )
    await act(async () => {})
    const stale = state.current.prepare
    act(() => {
      void stale(undefined, undefined, undefined, true)
    })
    rerender({ later: true })
    await act(async () => {
      finish({ status: 'failed', market_conditions: true })
      await stale(undefined, undefined, undefined, true)
    })
    expect(state.current.response.requested_market_conditions).toBeUndefined()
    expect(state.current.result).toBe(result)
    expect(portfolioResearch.prepareAnalysis).toHaveBeenCalledTimes(1)
  })

  it('prevents a pinned report from preparing market conditions', async () => {
    const { result: state } = renderHook(() =>
      usePortfolioAnalysis('job', result, true, false, true)
    )
    await act(async () => {
      await state.current.prepare(undefined, undefined, undefined, true)
    })
    expect(portfolioResearch.analysis).not.toHaveBeenCalled()
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
  })

  it('restores benchmark progress after refresh and retains the descriptor for an explicit retry', async () => {
    vi.mocked(portfolioResearch.analysis)
      .mockResolvedValueOnce({ status: 'running', benchmark: niftyBenchmark })
      .mockResolvedValueOnce({
        status: 'failed',
        benchmark: niftyBenchmark,
        error: 'Broker offline',
      })
    vi.mocked(portfolioResearch.prepareAnalysis).mockResolvedValue({
      status: 'queued',
      benchmark: niftyBenchmark,
    })
    const { result: state } = renderHook(() => usePortfolioAnalysis('job', result, true, false))
    await act(async () => {})
    expect(state.current.busy).toBe(true)
    expect(state.current.response.requested_benchmark).toEqual(niftyBenchmark)
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(state.current.response.status).toBe('failed')
    expect(state.current.result).toBe(result)
    await act(async () => {
      await state.current.prepare()
    })
    expect(portfolioResearch.prepareAnalysis).toHaveBeenCalledExactlyOnceWith(
      'job',
      undefined,
      undefined,
      'selection',
      niftyBenchmark
    )
  })

  it('requests a benchmark only explicitly, retries identical inputs, and preserves saved analysis after failure', async () => {
    vi.mocked(portfolioResearch.analysis).mockResolvedValue({ status: 'missing' })
    vi.mocked(portfolioResearch.prepareAnalysis)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ status: 'queued' })
    const existing = {
      ...result,
      analysis: {
        version: 'saved',
        metrics: {},
        unavailable: {},
        charts: [],
        catalog: [],
        basis: [],
      },
    }
    const { result: state } = renderHook(() => usePortfolioAnalysis('job', existing, true, true))
    await act(async () => {})
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
    await act(async () => {
      await state.current.prepare(['a.stop_pct'], 'AAA', niftyBenchmark)
    })
    expect(state.current.response.status).toBe('failed')
    expect(state.current.response.requested_benchmark).toEqual(niftyBenchmark)
    expect(state.current.result).toBe(existing)
    await act(async () => {
      await state.current.prepare()
    })
    expect(vi.mocked(portfolioResearch.prepareAnalysis).mock.calls).toEqual([
      ['job', ['a.stop_pct'], 'AAA', 'validation', niftyBenchmark],
      ['job', ['a.stop_pct'], 'AAA', 'validation', niftyBenchmark],
    ])
  })

  it('rejects duplicate starts and late benchmark responses across accounts', async () => {
    vi.mocked(portfolioResearch.analysis).mockResolvedValue({ status: 'missing' })
    let finish!: (value: Awaited<ReturnType<typeof portfolioResearch.prepareAnalysis>>) => void
    vi.mocked(portfolioResearch.prepareAnalysis).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    const { result: state } = renderHook(() => usePortfolioAnalysis('job', result, true, false))
    await act(async () => {})
    const stale = state.current.prepare
    act(() => {
      void stale(undefined, undefined, niftyBenchmark)
      void stale(undefined, undefined, niftyBenchmark)
    })
    expect(portfolioResearch.prepareAnalysis).toHaveBeenCalledTimes(1)
    await act(async () => {
      useAuthStore.setState({ user: { username: 'another-account' } })
    })
    await act(async () => {
      finish({
        status: 'complete',
        job: {
          id: 'job',
          status: 'completed',
          progress: 100,
          created_at: 1,
          result: { ...result, summary: { leaked: 99 } },
        },
      })
      await stale(undefined, undefined, niftyBenchmark)
    })
    expect(state.current.result).toBe(result)
    expect(state.current.response.requested_benchmark).toBeUndefined()
    expect(portfolioResearch.prepareAnalysis).toHaveBeenCalledTimes(1)
  })

  it('never replaces saved evidence with a result included in a failed benchmark receipt', async () => {
    vi.mocked(portfolioResearch.analysis).mockResolvedValue({ status: 'missing' })
    vi.mocked(portfolioResearch.prepareAnalysis).mockResolvedValue({
      status: 'failed',
      job: {
        id: 'job',
        status: 'failed',
        progress: 10,
        created_at: 1,
        result: { ...result, summary: { incomplete: 5 } },
      },
    })
    const { result: state } = renderHook(() => usePortfolioAnalysis('job', result, true, false))
    await act(async () => {})
    await act(async () => {
      await state.current.prepare(undefined, undefined, niftyBenchmark)
    })
    expect(state.current.result).toBe(result)
  })

  it('does not let a pinned report download a benchmark', async () => {
    const { result: state } = renderHook(() =>
      usePortfolioAnalysis('job', result, true, false, true)
    )
    await act(async () => {
      await state.current.prepare(undefined, undefined, niftyBenchmark)
    })
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
  })

  it('freezes the original analysis when the same job previously loaded newer analysis', async () => {
    const newer = { ...result, summary: { net_return_pct: 99 } }
    vi.mocked(portfolioResearch.analysis).mockResolvedValue({
      status: 'complete',
      job: { id: 'job', status: 'completed', progress: 100, created_at: 0, result: newer },
    })
    const { result: state, rerender } = renderHook(
      ({ frozen }) => usePortfolioAnalysis('job', result, true, false, frozen),
      { initialProps: { frozen: false } }
    )
    await act(async () => {})
    expect(state.current.result).toBe(newer)
    const stalePrepare = state.current.prepare
    rerender({ frozen: true })
    expect(state.current.result).toBe(result)
    await act(async () => {
      await stalePrepare()
      await state.current.prepare()
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(portfolioResearch.analysis).toHaveBeenCalledTimes(1)
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
    expect(state.current.busy).toBe(false)
  })

  it('stops queued polling and ignores pending responses when switching to a frozen report', async () => {
    vi.mocked(portfolioResearch.analysis).mockResolvedValueOnce({ status: 'queued' })
    let resolve!: (value: Awaited<ReturnType<typeof portfolioResearch.analysis>>) => void
    vi.mocked(portfolioResearch.analysis).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    const { result: state, rerender } = renderHook(
      ({ frozen }) => usePortfolioAnalysis('job', result, true, true, frozen),
      { initialProps: { frozen: false } }
    )
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    const signal = vi.mocked(portfolioResearch.analysis).mock.calls[1][1]!
    rerender({ frozen: true })
    expect(signal.aborted).toBe(true)
    await act(async () => {
      resolve({ status: 'queued' })
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(state.current.result).toBe(result)
    expect(state.current.busy).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
    expect(portfolioResearch.analysis).toHaveBeenCalledTimes(2)
  })

  it('never checks or prepares latest analysis for an initially frozen report', async () => {
    const { result: state } = renderHook(() =>
      usePortfolioAnalysis('job', result, true, false, true)
    )
    await act(async () => {
      await state.current.prepare()
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(state.current.result).toBe(result)
    expect(portfolioResearch.analysis).not.toHaveBeenCalled()
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
  })
  it('polls queued work, overlays completed analysis, and never alters the initial report', async () => {
    const enriched = {
      ...result,
      analysis: {
        version: 'v1',
        metrics: { sharpe: 1 },
        unavailable: {},
        catalog: [],
        charts: [],
        basis: [],
      },
    }
    vi.mocked(portfolioResearch.analysis)
      .mockResolvedValueOnce({ status: 'missing' })
      .mockResolvedValueOnce({ status: 'running' })
      .mockResolvedValueOnce({
        status: 'complete',
        job: { id: 'job', status: 'complete', progress: 100, created_at: 0, result: enriched },
      })
    vi.mocked(portfolioResearch.prepareAnalysis).mockResolvedValue({ status: 'queued' })
    const { result: state, unmount } = renderHook(() =>
      usePortfolioAnalysis('job', result, true, false)
    )
    await act(async () => {})
    await act(async () => {
      await state.current.prepare(['a.stop_pct', 'a.target_pct'])
    })
    expect(state.current.busy).toBe(true)
    expect(portfolioResearch.prepareAnalysis).toHaveBeenCalledExactlyOnceWith(
      'job',
      ['a.stop_pct', 'a.target_pct'],
      undefined,
      'selection'
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(state.current.response.status).toBe('running')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(state.current.result.analysis?.metrics.sharpe).toBe(1)
    expect(result.analysis).toBeUndefined()
    expect(state.current.busy).toBe(false)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('aborts status reads and clears queued timers when the report closes', async () => {
    vi.mocked(portfolioResearch.analysis).mockResolvedValue({ status: 'queued' })
    const { unmount } = renderHook(() => usePortfolioAnalysis('job', result, true, false))
    await act(async () => {})
    const signal = vi.mocked(portfolioResearch.analysis).mock.calls[0][1]!
    expect(vi.getTimerCount()).toBe(1)
    unmount()
    expect(signal.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(5000)
    expect(portfolioResearch.analysis).toHaveBeenCalledTimes(1)
  })

  it('keeps the last saved tear sheet when refreshing analysis fails', async () => {
    vi.mocked(portfolioResearch.analysis).mockResolvedValue({ status: 'missing' })
    vi.mocked(portfolioResearch.prepareAnalysis).mockRejectedValue(new Error('offline'))
    const existing = {
      ...result,
      analysis: { version: 'v1', metrics: {}, unavailable: {}, catalog: [], charts: [], basis: [] },
    }
    const { result: state } = renderHook(() => usePortfolioAnalysis('job', existing, true, false))
    await act(async () => {})
    await act(async () => {
      await state.current.prepare()
    })
    expect(state.current.response.status).toBe('failed')
    expect(state.current.result.analysis).toBe(existing.analysis)
    expect(state.current.busy).toBe(false)
  })

  it('requests the later period and displays its enriched result rather than the selected portfolio', async () => {
    vi.mocked(portfolioResearch.analysis).mockResolvedValue({ status: 'missing' })
    const later = {
      ...result,
      summary: { net_return_pct: 7 },
      analysis: {
        version: 'v1',
        metrics: {},
        unavailable: {},
        catalog: [],
        charts: [],
        basis: [],
        price_symbol: 'XYZ',
      },
    }
    const complete: PortfolioResult = {
      ...result,
      summary: { net_return_pct: 2 },
      validation: {
        result: later,
        train_from: '2026-01-01',
        train_to: '2026-05-01',
        test_from: '2026-05-02',
        test_to: '2026-06-01',
        training_signals: 10,
        testing_signals: 3,
        label: 'Later period',
        selection_basis: 'Earlier data',
      },
    }
    vi.mocked(portfolioResearch.prepareAnalysis).mockResolvedValue({
      status: 'complete',
      job: { id: 'job', status: 'completed', progress: 100, created_at: 0, result: complete },
    })
    const { result: state } = renderHook(() => usePortfolioAnalysis('job', result, true, true))
    await act(async () => {})
    await act(async () => {
      await state.current.prepare(undefined, 'XYZ')
    })
    expect(portfolioResearch.prepareAnalysis).toHaveBeenCalledExactlyOnceWith(
      'job',
      undefined,
      'XYZ',
      'validation'
    )
    expect(state.current.result.summary.net_return_pct).toBe(7)
    expect(state.current.result.analysis?.price_symbol).toBe('XYZ')
    expect(state.current.result.validation).toBeUndefined()
  })
})
