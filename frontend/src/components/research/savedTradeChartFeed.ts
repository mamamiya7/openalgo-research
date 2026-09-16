import type { Bar, BarsRequest, DataFeed, SeriesMarker } from 'openalgo-charts'
import type { SavedTradeChartPage, SavedTradeMarker } from '@/api/researchTradeChart'

/** One bounded response owns the chart; there is no route to a market-data API. */
export function createSavedTradeFeed(
  page: SavedTradeChartPage,
  cursor: () => number | null = () => null
): DataFeed {
  const bars = savedTradeBars(page)
  const read = (req: BarsRequest): Bar[] => {
    if (req.signal?.aborted) throw new DOMException('Chart closed', 'AbortError')
    if (
      req.symbol !== page.symbol ||
      req.exchange !== page.exchange ||
      req.interval !== page.interval
    ) {
      throw new Error('This chart uses the saved symbol and candle interval.')
    }
    const visible = cursor()
    return (visible === null ? bars : bars.slice(0, visible + 1))
      .filter(
        (bar) =>
          (req.from === undefined || bar.time >= req.from) &&
          (req.to === undefined || bar.time <= req.to)
      )
      .map((bar) => ({ ...bar }))
  }
  return {
    async getBars(req) {
      return read(req)
    },
    async getBarsPage(req) {
      const available = read(req).filter((bar) => bar.time < req.before)
      const count = Math.min(2000, Math.max(0, Math.floor(req.countBack)))
      const bars = count > 0 ? available.slice(-count) : []
      return { bars, hasMore: bars.length > 0 && available.length > bars.length }
    },
  }
}

export function savedTradeBars(page: SavedTradeChartPage): Bar[] {
  if (page.candles.length > 2000) throw new Error('Saved chart window is too large.')
  return page.candles.map((candle, index, all) => {
    if (
      ![candle.time, candle.open, candle.high, candle.low, candle.close].every(Number.isFinite) ||
      (candle.volume !== undefined && !Number.isFinite(candle.volume)) ||
      (index > 0 && candle.time <= all[index - 1].time)
    )
      throw new Error('Saved prices could not be read.')
    return {
      time: candle.time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      ...(candle.volume !== undefined ? { volume: candle.volume } : {}),
    }
  })
}

/** The engine sizes windows by wall-clock intervals, including overnight gaps. */
export function savedTradeLookback(page: SavedTradeChartPage): number {
  const first = page.candles[0]?.time
  const last = page.candles.at(-1)?.time
  if (first === undefined || last === undefined) return 1
  return Math.ceil((last - first) / (page.interval === 'D' ? 86_400 : 60)) + 2
}

export function visibleTradeMarkers(
  page: SavedTradeChartPage,
  cursor: number | null
): SavedTradeMarker[] {
  const lastIndex = cursor ?? page.candles.length - 1
  return page.markers.filter((marker) => {
    const local = marker.bar_index - page.window.offset
    return (
      local >= 0 &&
      local <= lastIndex &&
      page.candles[local]?.time === marker.time &&
      Number.isFinite(marker.price)
    )
  })
}

export function tradeSeriesMarkers(
  page: SavedTradeChartPage,
  cursor: number | null
): SeriesMarker[] {
  return visibleTradeMarkers(page, cursor).map((marker) => ({
    id: `${page.identity.trade_index}-${marker.kind}`,
    time: marker.time,
    price: marker.price,
    position: 'atPrice',
    shape: marker.kind === 'entry' ? 'labelUp' : 'labelDown',
    size: 'small',
    color: marker.kind === 'entry' ? '#059669' : '#e11d48',
    text: `${marker.kind === 'entry' ? 'Entry' : 'Exit'} ${marker.price.toLocaleString('en-IN', { maximumFractionDigits: 4 })}`,
  }))
}
