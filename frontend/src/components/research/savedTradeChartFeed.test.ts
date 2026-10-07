import { describe, expect, it } from 'vitest'
import {
  createSavedTradeFeed,
  savedTradeBars,
  savedTradeLookback,
  tradeSeriesMarkers,
  visibleTradeMarkers,
} from './savedTradeChartFeed'
import { savedTradeChartFixture } from './savedTradeChartFixture'

describe('frozen trade chart feed', () => {
  it('honours bounded date windows, never mutates the receipt, and provides no live source', async () => {
    const page = savedTradeChartFixture()
    const feed = createSavedTradeFeed(page)
    const req = {
      symbol: page.symbol,
      exchange: page.exchange,
      interval: page.interval,
      from: page.candles[1].time,
      to: page.candles[2].time,
    }
    const result = await feed.getBars(req)
    expect(result.map((bar) => bar.time)).toEqual(page.candles.slice(1, 3).map((bar) => bar.time))
    result[0].close = -1
    expect((await feed.getBars(req))[0].close).toBe(106)
    expect(page.candles[1].close).toBe(106)
    expect(feed.subscribeBars).toBeUndefined()
    expect(feed.subscribeDepth).toBeUndefined()
    expect(await feed.getBars({ ...req, from: page.candles[3].time + 1 })).toEqual([])
  })

  it('rejects cancellation and symbol, exchange, or interval changes', async () => {
    const page = savedTradeChartFixture()
    const feed = createSavedTradeFeed(page)
    const req = { symbol: page.symbol, exchange: page.exchange, interval: page.interval }
    const abort = new AbortController()
    abort.abort()
    await expect(feed.getBars({ ...req, signal: abort.signal })).rejects.toHaveProperty(
      'name',
      'AbortError'
    )
    for (const change of [{ symbol: 'OTHER' }, { exchange: 'BSE' }, { interval: '1m' }]) {
      await expect(feed.getBars({ ...req, ...change })).rejects.toThrow(
        'saved symbol and candle interval'
      )
    }
  })

  it('bounds every refresh and older-page read to the replay cursor', async () => {
    const page = savedTradeChartFixture()
    let cursor: number | null = 1
    const feed = createSavedTradeFeed(page, () => cursor)
    const req = { symbol: page.symbol, exchange: page.exchange, interval: page.interval }
    expect(await feed.getBars(req)).toHaveLength(2)
    expect(
      (await feed.getBarsPage!({ ...req, before: Infinity, countBack: 1500 })).bars
    ).toHaveLength(2)
    cursor = 0
    expect(await feed.getBars(req)).toHaveLength(1)
    cursor = null
    expect(await feed.getBars(req)).toHaveLength(4)
  })

  it('includes sparse daily sessions and overnight minute gaps in its opening span', () => {
    const daily = savedTradeChartFixture()
    expect(savedTradeLookback(daily)).toBe(8)
    const minute = { ...daily, interval: '1m' as const }
    const span = savedTradeLookback(minute) * 60
    expect(minute.candles.at(-1)!.time - span).toBeLessThan(minute.candles[0].time)
  })

  it('rejects oversized and corrupted candle evidence instead of inventing a candle', () => {
    const page = savedTradeChartFixture()
    expect(() => savedTradeBars({ ...page, candles: Array(2001).fill(page.candles[0]) })).toThrow(
      'too large'
    )
    expect(() => savedTradeBars({ ...page, candles: [page.candles[0], page.candles[0]] })).toThrow(
      'could not be read'
    )
    expect(() => savedTradeBars({ ...page, candles: [{ ...page.candles[0], open: NaN }] })).toThrow(
      'could not be read'
    )
  })
})

describe('recorded fill markers', () => {
  it('uses the exact recorded fill price and reveals only reached bars', () => {
    const page = savedTradeChartFixture()
    expect(visibleTradeMarkers(page, 0)).toEqual([])
    expect(visibleTradeMarkers(page, 1).map((marker) => marker.kind)).toEqual(['entry'])
    expect(visibleTradeMarkers(page, 3).map((marker) => marker.kind)).toEqual(['entry', 'exit'])
    expect(tradeSeriesMarkers(page, 1)[0]).toMatchObject({
      time: page.markers[0].time,
      price: 101.25,
      position: 'atPrice',
      shape: 'labelUp',
    })
    expect(visibleTradeMarkers(page, 1).some((marker) => marker.kind === 'exit')).toBe(false)
  })

  it('never maps an absent timestamp to the nearest candle, including later pages', () => {
    const page = savedTradeChartFixture()
    expect(
      visibleTradeMarkers(
        { ...page, markers: [{ ...page.markers[0], time: page.markers[0].time + 60 }] },
        null
      )
    ).toEqual([])
    const later = { ...page, candles: page.candles.slice(2), window: { ...page.window, offset: 2 } }
    expect(visibleTradeMarkers(later, 0)).toEqual([])
    expect(visibleTradeMarkers(later, 1).map((marker) => marker.kind)).toEqual(['exit'])
  })
})
