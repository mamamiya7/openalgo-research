import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Bar, DataFeed, SeriesMarker } from 'openalgo-charts'
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
}
const harness = vi.hoisted(() => ({
  widgets: [] as StubWidget[],
  props: [] as Record<string, unknown>[],
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
          addIndicator: vi.fn(),
          indicators: () => [
            {
              updateLegendValues: () => {
                widget.indicatorReadoutLengths.push(widget.bars.length)
              },
            },
          ],
          setVisibleLogicalRange: vi.fn(),
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
})
afterEach(() => {
  vi.useRealTimers()
})

describe('saved native trade chart', () => {
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
