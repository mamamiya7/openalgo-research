import { type Chart, getIndicator } from 'openalgo-charts'
import { ATR, BOLLINGER, EMA, MACD, RSI, SMA, VOLUME, VWAP } from 'openalgo-charts/indicators'

/** These studies consume this chart's candles; none fetches an external series. */
export const SAVED_TRADE_INDICATORS = [EMA, SMA, RSI, MACD, BOLLINGER, ATR, VOLUME, VWAP]

/**
 * The native registry is global and a user script can shadow a built-in ID.
 * Check the actual descriptor before native synchronous instance construction.
 * No registry swap, user script import, or saved arbitrary layout is involved.
 */
export function guardSavedTradeIndicators(chart: Chart): () => void {
  const original = chart.addIndicator
  const guarded: Chart['addIndicator'] = (id, settings, options) => {
    const builtin = SAVED_TRADE_INDICATORS.find((item) => item.id === id)
    if (!builtin || getIndicator(id) !== builtin) {
      throw new Error('This chart supports unmodified built-in indicators.')
    }
    return original.call(chart, id, settings, options)
  }
  chart.addIndicator = guarded
  return () => {
    if (chart.addIndicator === guarded) chart.addIndicator = original
  }
}
