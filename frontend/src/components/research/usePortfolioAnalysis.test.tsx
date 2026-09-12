import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type PortfolioResult, portfolioResearch } from '@/api/portfolioResearch'
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
})
afterEach(() => {
  vi.useRealTimers()
})

describe('saved analysis lifecycle', () => {
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
