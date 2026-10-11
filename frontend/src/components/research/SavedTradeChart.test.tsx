import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { type Bar, type DataFeed, ReplayController, type SeriesMarker } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SavedTradeChart from './SavedTradeChart'
import { savedTradeChartFixture } from './savedTradeChartFixture'

interface StubWidget {
  bars: Bar[]
  markers: readonly SeriesMarker[]
  removed: boolean
  nativeSourceSnapshots: Bar[][]
  indicatorReadoutLengths: number[]
  destroyed: boolean
  chart: Record<string, unknown>
  series: Record<string, unknown>
  studies: Array<{
    indicatorId: string
    settings: () => Record<string, unknown>
    setSettings: (patch: Record<string, unknown>) => void
    visible: () => boolean
    setVisible: (visible: boolean) => void
    updateLegendValues: () => void
  }>
  indicatorSourceSnapshots: Bar[][]
}
const harness = vi.hoisted(() => ({
  widgets: [] as StubWidget[],
  props: [] as Record<string, unknown>[],
  readings: [] as { destroy: ReturnType<typeof vi.fn> }[],
}))

vi.mock('@/components/chart/mountChartDataWindow', () => ({
  mountChartDataWindow: () => {
    const readings = { destroy: vi.fn() }
    harness.readings.push(readings)
    return readings
  },
}))

vi.mock('@/components/chart/OpenAlgoChart', async () => {
  const React = await import('react')
  return {
    OpenAlgoChart: (props: {
      feed: DataFeed
      symbol: string
      exchange: string
      interval: string
      onReady: (widget: unknown) => void
      onData: (event: unknown) => void
    }) => {
      const latest = React.useRef(props)
      latest.current = props
      React.useEffect(() => {
        let gone = false
        const widget: StubWidget = {
          bars: [],
          markers: [],
          removed: false,
          destroyed: false,
          nativeSourceSnapshots: [],
          indicatorReadoutLengths: [],
          studies: [],
          indicatorSourceSnapshots: [],
          chart: {},
          series: {},
        }
        const markers = {
          setMarkers: (next: SeriesMarker[]) => {
            widget.markers = next
          },
        }
        widget.series = {
          getData: () => widget.bars,
          setData: (bars: Bar[]) => {
            widget.bars = bars
            // All native indicator calculations consume precisely this source.
            widget.nativeSourceSnapshots.push(bars.slice())
          },
          createMarkers: () => markers,
        }
        widget.chart = {
          addIndicator: vi.fn((indicatorId: string, inputs = {}) => {
            let settings = { ...inputs }
            let visible = true
            const study = {
              indicatorId,
              settings: () => ({ ...settings }),
              setSettings: (patch: Record<string, unknown>) => {
                settings = { ...settings, ...patch }
              },
              visible: () => visible,
              setVisible: (next: boolean) => {
                visible = next
              },
              updateLegendValues: () => widget.indicatorReadoutLengths.push(widget.bars.length),
            }
            widget.studies.push(study)
            widget.indicatorSourceSnapshots.push(widget.bars.slice())
            return study
          }),
          indicators: () => [
            ...widget.studies,
            {
              updateLegendValues: () => {
                widget.indicatorReadoutLengths.push(widget.bars.length)
              },
            },
          ],
          setVisibleLogicalRange: vi.fn(),
          setTimeFormatter: vi.fn(),
          removePrimitive: () => {
            widget.removed = true
          },
          emit: vi.fn(),
          timeScale: {
            barSpacing: 6,
            rightOffset: 1,
            setBarSpacing: vi.fn(),
            setRightOffset: vi.fn(),
          },
        }
        harness.widgets.push(widget)
        harness.props.push(props as unknown as Record<string, unknown>)
        latest.current.onReady(widget)
        void props.feed
          .getBars({ symbol: props.symbol, exchange: props.exchange, interval: props.interval })
          .then((bars) => {
            if (gone) return
            widget.bars = bars
            latest.current.onData({ bars: bars.length })
          })
        return () => {
          gone = true
          widget.destroyed = true
          latest.current.onReady(null)
        }
      }, [props.feed])
      return <section aria-label="Native chart" />
    },
  }
})

const latest = () => harness.widgets.at(-1)!
beforeEach(() => {
  harness.widgets.length = 0
  harness.props.length = 0
  harness.readings.length = 0
})
afterEach(() => {
  vi.useRealTimers()
})

describe('saved native trade chart', () => {
  it.each([
    1, 1000,
  ])('fits a saved %s-candle range without changing candles or inventing a missing exit', async (length) => {
    const page = savedTradeChartFixture()
    page.candles = Array.from({ length }, (_, index) => ({
      ...page.candles[0],
      time: page.candles[0].time + index * 86400,
      timestamp: new Date((page.candles[0].time + index * 86400) * 1000).toISOString().slice(0, 10),
    }))
    page.markers = []
    page.window = { ...page.window, total: length, entry_index: null, exit_index: null }
    render(<SavedTradeChart page={page} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fit candles' })).toBeEnabled())
    await waitFor(() => expect(latest().chart.setVisibleLogicalRange).toHaveBeenCalled())
    const fit = vi
      .mocked(latest().chart.setVisibleLogicalRange as ReturnType<typeof vi.fn>)
      .mock.calls.at(-1)![0] as { from: number; to: number }
    expect(fit.from).toBeLessThanOrEqual(0)
    expect(fit.to).toBeGreaterThanOrEqual(length - 1)
    expect(fit.to).toBeLessThan(length + 21)
    expect(latest().bars.map((bar) => bar.time)).toEqual(page.candles.map((bar) => bar.time))
    expect(latest().markers).toHaveLength(0)
    if (length === 1) expect(screen.getByRole('button', { name: 'Replay' })).toBeDisabled()
  })

  it('fits recorded candles on open and fits only the revealed prefix during replay', async () => {
    const page = savedTradeChartFixture()
    render(<SavedTradeChart page={page} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fit candles' })).toBeEnabled())
    await waitFor(() =>
      expect(latest().chart.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: -1, to: 4 })
    )
    fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next bar' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Fit candles' }))
    expect(latest().bars).toHaveLength(2)
    expect(latest().chart.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: -1, to: 2 })
    fireEvent.click(screen.getByRole('button', { name: 'Next bar' }))
    fireEvent.click(screen.getByRole('button', { name: 'Fit candles' }))
    expect(latest().chart.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: -1, to: 3 })
    expect(screen.queryByText(/108.75/)).not.toBeInTheDocument()
  })

  it('formats daily coordinates as session dates without modifying recorded bars or execution markers', async () => {
    const page = savedTradeChartFixture()
    render(<SavedTradeChart page={page} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay' })).toBeEnabled())
    const formatter = vi.mocked(latest().chart.setTimeFormatter as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as (time: number) => string
    expect(formatter(page.candles[1].time)).toBe('23 Jan 2026')
    expect(latest().bars.map((bar) => bar.time)).toEqual(page.candles.map((bar) => bar.time))
    expect(latest().markers.map((marker) => marker.time)).toEqual(
      page.markers.map((marker) => marker.time)
    )
    expect(screen.getByText(/Daily chart labels show session dates/)).toBeVisible()
  })

  it('carries only study preferences across review/replay, recomputing on the loaded prefix and clearing on a new trade', async () => {
    const page = savedTradeChartFixture()
    const view = render(<SavedTradeChart page={page} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Indicators' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Indicators' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'EMA', exact: true }))
    const original = latest()
    original.studies[0].setSettings({
      length: 3,
      source: 'close',
      color: '#ff0000',
      'ma:width': 3,
      'ma:lineStyle': 'dashed',
    })
    original.studies[0].setVisible(false)
    fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next bar' })).toBeEnabled())
    const replay = latest()
    expect(original.destroyed).toBe(true)
    expect(replay.studies).toHaveLength(1)
    expect(replay.studies[0]).not.toBe(original.studies[0])
    expect(replay.studies[0].settings()).toEqual(original.studies[0].settings())
    expect(replay.studies[0].visible()).toBe(false)
    expect(replay.indicatorSourceSnapshots[0]).toHaveLength(2)
    expect(replay.indicatorSourceSnapshots[0].map((bar) => bar.time)).toEqual(
      page.candles.slice(0, 2).map((bar) => bar.time)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Next bar' }))
    fireEvent.click(screen.getByRole('button', { name: 'Previous bar' }))
    expect(replay.indicatorReadoutLengths.at(-1)).toBe(2)
    fireEvent.click(screen.getAllByRole('button', { name: 'Exit replay' })[0])
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay' })).toBeEnabled())
    await waitFor(() => expect(latest().studies).toHaveLength(1))
    expect(latest().studies[0].settings()).toEqual(original.studies[0].settings())
    const restored = latest()
    view.rerender(
      <SavedTradeChart
        page={{
          ...page,
          identity: { ...page.identity, trade_index: page.identity.trade_index + 1 },
        }}
        onPage={vi.fn()}
      />
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay' })).toBeEnabled())
    expect(restored.destroyed).toBe(true)
    expect(latest().studies).toHaveLength(0)
  })

  it('opens Values only on request and releases it on close, replay rebuild and unmount', async () => {
    const page = savedTradeChartFixture()
    const view = render(<SavedTradeChart page={page} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Values' })).toBeEnabled())
    expect(screen.getByRole('button', { name: 'Values' })).toHaveAttribute('aria-expanded', 'false')
    expect(harness.readings).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Values' }))
    expect(screen.getByRole('complementary', { name: 'Saved chart values' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Values' })).toHaveAttribute(
      'aria-controls',
      screen.getByRole('complementary').id
    )
    expect(harness.readings).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Values' }))
    expect(harness.readings[0].destroy).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Values' }))
    const reviewReadings = harness.readings.at(-1)!
    fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next bar' })).toBeEnabled())
    expect(reviewReadings.destroy).toHaveBeenCalledTimes(1)
    expect(harness.readings.at(-1)).not.toBe(reviewReadings)
    const replayReadings = harness.readings.at(-1)!
    view.unmount()
    expect(replayReadings.destroy).toHaveBeenCalledTimes(1)
  })

  it('keeps native replay on observed completed candles without simulated intrabar prices', async () => {
    const state = vi.spyOn(ReplayController.prototype, 'state')
    try {
      const page = savedTradeChartFixture()
      render(<SavedTradeChart page={page} onPage={vi.fn()} />)
      await waitFor(() => expect(screen.getByRole('button', { name: 'Replay' })).toBeEnabled())
      fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
      await waitFor(() => expect(screen.getByRole('button', { name: 'Next bar' })).toBeEnabled())
      fireEvent.click(screen.getByRole('button', { name: 'Next bar' }))
      expect(state).toHaveBeenCalled()
      expect(state.mock.results.every((result) => result.value?.simulated === false)).toBe(true)
      expect(latest().bars.map((bar) => bar.close)).toEqual(
        page.candles.slice(0, 3).map((bar) => bar.close)
      )
    } finally {
      state.mockRestore()
    }
  })

  it('only offers volume studies when every saved candle retains volume', async () => {
    const user = userEvent.setup()
    const page = savedTradeChartFixture()
    const view = render(<SavedTradeChart page={page} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Indicators' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Indicators' }))
    expect(screen.getByRole('menuitem', { name: 'EMA' })).toBeVisible()
    expect(screen.queryByRole('menuitem', { name: 'Volume', exact: true })).not.toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'VWAP', exact: true })).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    view.unmount()
    const withVolume = { ...page, candles: page.candles.map((bar) => ({ ...bar, volume: 10 })) }
    render(<SavedTradeChart page={withVolume} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Indicators' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Indicators' }))
    expect(screen.getByRole('menuitem', { name: 'Volume', exact: true })).toBeVisible()
    expect(screen.getByRole('menuitem', { name: 'VWAP', exact: true })).toBeVisible()
  })

  it('keeps recorded minute fill time distinct from the plotted bar start', async () => {
    const page = savedTradeChartFixture()
    page.interval = '1m'
    page.markers[0].timestamp = '2026-01-23T09:16:00+05:30'
    render(<SavedTradeChart page={page} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay' })).toBeEnabled())
    expect(screen.getByText(/23 Jan 2026, 09:16/)).toBeVisible()
    expect(latest().markers[0].time).toBe(page.candles[1].time)
  })

  it('rebuilds a clean workspace and uses the real native replay controller to hide future prices and exit text', async () => {
    const page = savedTradeChartFixture()
    render(<SavedTradeChart page={page} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay' })).toBeEnabled())
    const reviewWidget = latest()
    expect(reviewWidget.markers).toHaveLength(2)
    expect(screen.getByText(/108.75/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next bar' })).toBeEnabled())
    expect(latest()).not.toBe(reviewWidget)
    expect(reviewWidget.destroyed).toBe(true)
    expect(reviewWidget.removed).toBe(true)
    expect(latest().bars).toHaveLength(2)
    expect(latest().markers.map((m) => m.text)).toEqual(['Entry 101.25'])
    expect(screen.queryByText(/108.75/)).not.toBeInTheDocument()
    expect(harness.props.at(-1)).toMatchObject({
      rail: false,
      indicators: false,
      customIndicators: false,
      statusline: false,
    })
    const feed = harness.props.at(-1)!.feed as DataFeed
    expect(
      await feed.getBars({ symbol: page.symbol, exchange: page.exchange, interval: page.interval })
    ).toHaveLength(2)

    fireEvent.click(screen.getByRole('button', { name: 'Next bar' }))
    expect(latest().bars).toHaveLength(3)
    expect(screen.queryByText(/108.75/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next bar' }))
    expect(latest().bars).toHaveLength(4)
    expect(screen.getByText(/108.75/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Previous bar' }))
    expect(latest().bars).toHaveLength(3)
    expect(latest().nativeSourceSnapshots.at(-1)).toHaveLength(3)
    expect(latest().indicatorReadoutLengths.at(-1)).toBe(3)
    expect(latest().chart.emit).toHaveBeenLastCalledWith('crosshair:readout', {
      index: 2,
      point: null,
      time: page.candles[2].time,
    })
    expect(latest().markers).toHaveLength(1)
    expect(screen.queryByText(/108.75/)).not.toBeInTheDocument()
  })

  it('stops playback on unmount and restores the full page in a new workspace when leaving replay', async () => {
    const page = savedTradeChartFixture()
    const view = render(<SavedTradeChart page={page} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled())
    fireEvent.click(screen.getAllByRole('button', { name: 'Exit replay' })[0])
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay' })).toBeEnabled())
    expect(latest().bars).toHaveLength(4)
    expect(latest().markers).toHaveLength(2)
    expect(harness.props.at(-1)).toMatchObject({ statusline: true })
    fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled())
    vi.useFakeTimers()
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
    expect(harness.widgets.every((widget) => widget.destroyed && widget.removed)).toBe(true)
  })

  it('pauses automatic playback when the tab is hidden', async () => {
    render(<SavedTradeChart page={savedTradeChartFixture()} onPage={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Replay' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Replay' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    expect(screen.getByRole('button', { name: 'Play' })).toBeVisible()
    hidden.mockRestore()
  })

  it('labels paged replay explicitly and pages by the returned offsets', async () => {
    const page = savedTradeChartFixture()
    page.window.total = 4000
    page.window.next_offset = 1500
    const onPage = vi.fn()
    render(<SavedTradeChart page={page} onPage={onPage} />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Replay section' })).toBeEnabled()
    )
    expect(screen.getByText('Candles 1–4 of 4,000')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Earlier' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Later' }))
    expect(onPage).toHaveBeenCalledWith(1500)
  })
})
