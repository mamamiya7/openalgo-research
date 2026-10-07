import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { type Bar, createChart, ReplayController, registerIndicator } from 'openalgo-charts'
import 'openalgo-charts/indicators'
import { EMA } from 'openalgo-charts/indicators'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { savedStudyPreferences } from './SavedTradeChart'
import { resetSavedTradeReadout, SavedTradeValues } from './SavedTradeValues'
import { guardSavedTradeIndicators } from './savedTradeIndicators'

const T = Date.UTC(2026, 0, 5) / 1000
const bars: Bar[] = [100, 110, 120, 130].map((close, index) => ({
  time: T + index * 86400,
  open: close - 1,
  high: close + 2,
  low: close - 3,
  close,
}))

beforeEach(() => {
  const context = new Proxy(
    {
      measureText: (text: string) => ({ width: text.length * 7 }),
      createLinearGradient: () => ({ addColorStop() {} }),
      getImageData: () => ({ data: new Uint8ClampedArray([0, 0, 0, 255]) }),
    },
    { get: (target, key) => target[key as keyof typeof target] ?? (() => {}) }
  )
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    context as unknown as CanvasRenderingContext2D
  )
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function chart(data = bars) {
  const chart = createChart(document.createElement('div'), {
    shortcuts: false,
    timeNavigator: false,
    raf: {
      schedule: (cb) => {
        cb()
        return 1
      },
      cancel() {},
    },
  })
  chart.applySize(800, 600)
  chart.setDataContext({ symbol: 'SBIN', exchange: 'NSE', interval: 'D' })
  const series = chart.addSeries('candlestick')
  series.setData(data)
  return { chart, series }
}
const value = (key: string) =>
  Number(
    document
      .querySelector(`[data-key="${key}"] .oac-data-window__value`)
      ?.textContent?.replace(/,/g, '')
  )

describe('saved trade native Values panel', () => {
  it('recreates native EMA parameters and styles on the new prefix without retaining its future values', async () => {
    const review = chart()
    const original = review.chart.addIndicator('ema', {
      length: 3,
      source: 'close',
      color: '#ff0000',
      'ma:width': 3,
      'ma:lineStyle': 'dashed',
    })
    const preferences = savedStudyPreferences(review.chart)
    original.updateLegendValues(3)
    const futureValue = original.values().ma[3]
    review.chart.destroy()
    const native = chart(bars.slice(0, 2))
    const restoreGuard = guardSavedTradeIndicators(native.chart)
    const restored = native.chart.addIndicator(preferences[0].indicatorId, preferences[0].settings)
    expect(restored.settings()).toMatchObject({
      length: 3,
      color: '#ff0000',
      'ma:width': 3,
      'ma:lineStyle': 'dashed',
    })
    const view = render(<SavedTradeValues chart={native.chart} id="saved-values" />)
    const replay = new ReplayController(native.chart, {
      series: native.series,
      bars,
      startIndex: 1,
      onFrame: (frame) => {
        for (const indicator of native.chart.indicators()) indicator.updateLegendValues(frame.index)
        resetSavedTradeReadout(native.chart, frame)
      },
    })
    try {
      await waitFor(() => expect(value('close')).toBe(110))
      expect(restored.values().ma).toHaveLength(2)
      expect(value(`${restored.id}:ma`)).not.toBe(futureValue)
      act(() => replay.seek(3))
      await waitFor(() => expect(value(`${restored.id}:ma`)).toBe(futureValue))
      native.chart.emit('crosshair:readout', {
        index: 3,
        point: { x: 2, y: 2 },
        time: bars[3].time,
      })
      act(() => replay.seek(0))
      await waitFor(() => expect(value('close')).toBe(100))
      expect(restored.values().ma).toHaveLength(1)
      expect(value(`${restored.id}:ma`)).not.toBe(futureValue)
    } finally {
      view.unmount()
      replay.stop()
      restoreGuard()
      native.chart.destroy()
    }
  })

  it('drops invalid parameters, cached values, study-output references and shadowed custom definitions', () => {
    const native = chart()
    const study = native.chart.addIndicator('ema')
    vi.spyOn(study, 'settings').mockReturnValue({
      length: Number.POSITIVE_INFINITY,
      source: { instanceId: 'custom', plot: 'future' },
      color: '#ff0000',
      'ma:width': 999,
      'ma:lineStyle': 'invalid',
      timeframe: 'not-a-timeframe',
      values: [100, 110, 120, 130],
      drawings: ['future exit'],
      custom: () => true,
    })
    try {
      expect(savedStudyPreferences(native.chart)).toEqual([
        { indicatorId: 'ema', settings: { color: '#ff0000' }, visible: true },
      ])
      registerIndicator({ ...EMA, calc: vi.fn() })
      expect(savedStudyPreferences(native.chart)).toEqual([])
    } finally {
      registerIndicator(EMA)
      native.chart.destroy()
    }
  })

  it('discards future hover and study readings when native replay rewinds', async () => {
    const native = chart()
    const sma = native.chart.addIndicator('sma', { length: 3 })
    const view = render(<SavedTradeValues chart={native.chart} id="saved-values" />)
    const replay = new ReplayController(native.chart, {
      series: native.series,
      bars,
      startIndex: 3,
      onFrame: (frame) => {
        for (const indicator of native.chart.indicators()) indicator.updateLegendValues(frame.index)
        resetSavedTradeReadout(native.chart, frame)
      },
    })
    try {
      native.chart.emit('crosshair:readout', {
        index: 3,
        point: { x: 100, y: 20 },
        time: bars[3].time,
      })
      await waitFor(() => expect(value('close')).toBe(130))
      await waitFor(() => expect(value(`${sma.id}:ma`)).toBe(120))
      act(() => replay.seek(1))
      await waitFor(() => expect(value('close')).toBe(110))
      expect(
        document.querySelector(`[data-key^="${sma.id}:"] .oac-data-window__value`)
      ).toHaveClass('is-unavailable')
      expect(native.series.getData()).toHaveLength(2)
      expect(screen.getByRole('complementary')).not.toHaveTextContent('130')
      act(() => replay.seek(2))
      await waitFor(() => expect(value('close')).toBe(120))
      expect(value(`${sma.id}:ma`)).toBe(110)
      act(() => {
        native.chart.emit('crosshair:readout', {
          index: 2,
          point: { x: 100, y: 20 },
          time: bars[2].time,
        })
        replay.seek(0)
      })
      await waitFor(() => expect(value('close')).toBe(100))
      expect(['open', 'high', 'low', 'close'].map(value)).toEqual([99, 102, 97, 100])
      expect(
        document.querySelector(`[data-key^="${sma.id}:"] .oac-data-window__value`)
      ).toHaveClass('is-unavailable')
    } finally {
      view.unmount()
      replay.stop()
      native.chart.destroy()
    }
  })

  it('releases all native reading subscriptions across 100 open/close cycles, including queued updates', async () => {
    const native = chart()
    const subscribe = native.chart.on.bind(native.chart)
    let retained = 0
    let styleCount: number | undefined
    vi.spyOn(native.chart, 'on').mockImplementation((event, callback) => {
      retained++
      const off = subscribe(event, callback)
      let active = true
      return () => {
        if (active) {
          retained--
          active = false
        }
        off()
      }
    })
    try {
      for (let cycle = 0; cycle < 100; cycle++) {
        const view = render(<SavedTradeValues chart={native.chart} id="saved-values" />)
        styleCount ??= document.head.querySelectorAll('style').length
        expect(document.head.querySelectorAll('style')).toHaveLength(styleCount)
        expect(retained).toBeGreaterThan(0)
        native.chart.emit('crosshair:readout', { index: 0, point: null, time: T })
        view.unmount()
        await act(async () => {})
        expect(retained).toBe(0)
        expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
      }
    } finally {
      native.chart.destroy()
    }
  }, 30000)

  it('rebinds to a new frozen chart and empties on chart destruction', async () => {
    const first = chart()
    const second = chart([{ ...bars[0], open: 999, high: 999, low: 999, close: 999 }])
    const view = render(<SavedTradeValues chart={first.chart} id="saved-values" />)
    first.chart.emit('crosshair:readout', { index: 0, point: null, time: T })
    await waitFor(() => expect(value('close')).toBe(100))
    view.rerender(<SavedTradeValues chart={second.chart} id="saved-values" />)
    expect(value('close')).toBe(999)
    first.chart.destroy()
    second.chart.destroy()
    expect(screen.getByRole('complementary').textContent).toBe('Values')
    view.unmount()
  })
})
