import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  defaultReportPreferences,
  type ReportPreferenceReceipt,
  reportPreferences,
} from '@/api/reportPreferences'
import { useReportPreferences } from './useReportPreferences'

vi.mock('@/api/reportPreferences', async (original) => ({
  ...(await original<typeof import('@/api/reportPreferences')>()),
  reportPreferences: { get: vi.fn(), update: vi.fn() },
}))
const receipt = (
  revision = 0,
  performance_view: 'return' | 'equity' = 'return'
): ReportPreferenceReceipt => ({
  version: 'research-report-preferences-v1',
  revision,
  preferences: { ...defaultReportPreferences, performance_view },
  updated_at: revision ? 1 : null,
})
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
beforeEach(() => {
  vi.mocked(reportPreferences.get).mockReset().mockResolvedValue(receipt())
  vi.mocked(reportPreferences.update).mockReset()
})

describe('account report preferences', () => {
  it('loads saved views without posting defaults and writes only changed presentation fields', async () => {
    vi.mocked(reportPreferences.get).mockResolvedValue(receipt(4, 'equity'))
    const { result } = renderHook(() => useReportPreferences('alice'))
    await waitFor(() => expect(result.current.ready).toBe(true))
    expect(result.current.value.performance_view).toBe('equity')
    expect(reportPreferences.update).not.toHaveBeenCalled()
    await act(async () =>
      expect(await result.current.save({ performance_view: 'equity' })).toBe(true)
    )
    expect(reportPreferences.update).not.toHaveBeenCalled()
    vi.mocked(reportPreferences.update).mockResolvedValue(receipt(5, 'return'))
    await act(async () =>
      expect(await result.current.save({ performance_view: 'return' })).toBe(true)
    )
    expect(reportPreferences.update).toHaveBeenCalledWith(
      4,
      { performance_view: 'return' },
      expect.any(AbortSignal)
    )
    expect(result.current.value.performance_view).toBe('return')
  })

  it('serializes writes and retains the acknowledged value on failure', async () => {
    const pending = deferred<ReportPreferenceReceipt>()
    vi.mocked(reportPreferences.update).mockReturnValue(pending.promise)
    const { result } = renderHook(() => useReportPreferences('alice'))
    await waitFor(() => expect(result.current.ready).toBe(true))
    let first!: Promise<boolean>
    act(() => {
      first = result.current.save({ performance_view: 'equity' })
    })
    expect(result.current.saving).toBe(true)
    expect(result.current.value.performance_view).toBe('equity')
    await act(async () => expect(await result.current.save({ rolling_window: 63 })).toBe(false))
    expect(reportPreferences.update).toHaveBeenCalledTimes(1)
    await act(async () => {
      pending.reject(new Error('offline'))
      expect(await first).toBe(false)
    })
    expect(result.current.value.performance_view).toBe('return')
    expect(result.current.error).toContain('not saved')
    expect(result.current.saving).toBe(false)
  })

  it('does not overwrite another tab: reload is required before explicit reapply', async () => {
    vi.mocked(reportPreferences.update).mockRejectedValue({
      response: { status: 409, data: { current: receipt(1, 'equity') } },
    })
    const { result } = renderHook(() => useReportPreferences('alice'))
    await waitFor(() => expect(result.current.ready).toBe(true))
    await act(async () => {
      await result.current.save({ rolling_window: 63 })
    })
    expect(result.current.conflict).toBe(true)
    await act(async () => expect(await result.current.save({ rolling_window: 63 })).toBe(false))
    expect(reportPreferences.update).toHaveBeenCalledTimes(1)
    vi.mocked(reportPreferences.get).mockResolvedValue(receipt(1, 'equity'))
    await act(async () => {
      await result.current.reload()
    })
    expect(result.current.value.performance_view).toBe('equity')
    const saved = receipt(2, 'equity')
    saved.preferences.rolling_window = 63
    vi.mocked(reportPreferences.update).mockResolvedValue(saved)
    await act(async () => {
      await result.current.save({ rolling_window: 63 })
    })
    expect(reportPreferences.update).toHaveBeenLastCalledWith(
      1,
      { rolling_window: 63 },
      expect.any(AbortSignal)
    )
  })

  it('drops stale reads and aborts pending work when the account changes or view unmounts', async () => {
    const oldRead = deferred<ReportPreferenceReceipt>()
    vi.mocked(reportPreferences.get)
      .mockReturnValueOnce(oldRead.promise)
      .mockResolvedValue(receipt(7))
    const { result, rerender, unmount } = renderHook(({ owner }) => useReportPreferences(owner), {
      initialProps: { owner: 'alice' },
    })
    const oldSignal = vi.mocked(reportPreferences.get).mock.calls[0][0]!
    rerender({ owner: 'bob' })
    await waitFor(() => expect(result.current.ready).toBe(true))
    await act(async () => oldRead.resolve(receipt(55, 'equity')))
    expect(oldSignal.aborted).toBe(true)
    expect(result.current.value.performance_view).toBe('return')
    const write = deferred<ReportPreferenceReceipt>()
    vi.mocked(reportPreferences.update).mockReturnValue(write.promise)
    let save!: Promise<boolean>
    act(() => {
      save = result.current.save({ performance_view: 'equity' })
    })
    const signal = vi.mocked(reportPreferences.update).mock.calls[0][2]!
    unmount()
    expect(signal.aborted).toBe(true)
    await act(async () => {
      write.resolve(receipt(8, 'equity'))
      expect(await save).toBe(false)
    })
  })

  it('does not access account preferences until a user is available', () => {
    renderHook(() => useReportPreferences(undefined))
    expect(reportPreferences.get).not.toHaveBeenCalled()
  })
})
