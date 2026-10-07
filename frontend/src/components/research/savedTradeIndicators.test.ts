import { type Chart, getIndicator, registerIndicator } from 'openalgo-charts'
import { EMA } from 'openalgo-charts/indicators'
import { describe, expect, it, vi } from 'vitest'
import { guardSavedTradeIndicators } from './savedTradeIndicators'

describe('saved chart built-in indicator admission', () => {
  it('uses the native study implementation and restores its per-chart guard on disposal', () => {
    const native = vi.fn()
    const chart = { addIndicator: native } as unknown as Chart
    const dispose = guardSavedTradeIndicators(chart)
    chart.addIndicator(EMA.id)
    expect(native).toHaveBeenCalledWith(EMA.id, undefined, undefined)
    expect(() => chart.addIndicator('untrusted-custom')).toThrow('unmodified built-in')
    expect(native).toHaveBeenCalledTimes(1)
    dispose()
    expect(chart.addIndicator).toBe(native)
  })

  it('refuses a custom script shadowing a built-in ID without mutating the shared registry', () => {
    const existing = getIndicator(EMA.id)
    const native = vi.fn()
    const customized = { ...EMA, calc: vi.fn() }
    const chart = { addIndicator: native } as unknown as Chart
    const dispose = guardSavedTradeIndicators(chart)
    try {
      registerIndicator(customized)
      expect(() => chart.addIndicator(EMA.id)).toThrow('unmodified built-in')
      expect(native).not.toHaveBeenCalled()
      expect(customized.calc).not.toHaveBeenCalled()
      expect(getIndicator(EMA.id)).toBe(customized)
    } finally {
      registerIndicator(existing)
      dispose()
    }
  })
})
