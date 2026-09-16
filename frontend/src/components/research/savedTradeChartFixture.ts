import type { SavedTradeChartPage } from '@/api/researchTradeChart'

/** Controlled bars for component and feed acceptance, never broker evidence. */
export function savedTradeChartFixture(): SavedTradeChartPage {
  const times = [
    Date.UTC(2026, 0, 22),
    Date.UTC(2026, 0, 23),
    Date.UTC(2026, 0, 27),
    Date.UTC(2026, 0, 28),
  ]
  return {
    version: '1',
    identity: {
      job_id: 'job',
      result_artifact: 'a'.repeat(64),
      inputs_artifact: 'b'.repeat(64),
      period: 'full',
      trade_index: 0,
    },
    symbol: 'HINDZINC',
    exchange: 'NSE',
    interval: 'D',
    timezone: 'Asia/Kolkata',
    candles: times.map((time, index) => ({
      time: time / 1000,
      timestamp: new Date(time).toISOString().slice(0, 10),
      open: 100 + index,
      high: 110 + index,
      low: 95 + index,
      close: 105 + index,
    })),
    markers: [
      {
        kind: 'entry',
        time: times[1] / 1000,
        timestamp: '2026-01-23',
        price: 101.25,
        quantity: 10,
        basis: 'recorded',
        bar_index: 1,
      },
      {
        kind: 'exit',
        time: times[3] / 1000,
        timestamp: '2026-01-28',
        price: 108.75,
        quantity: 10,
        basis: 'recorded',
        bar_index: 3,
      },
    ],
    trade: { net_pnl: 75 },
    window: {
      offset: 0,
      limit: 1500,
      total: 4,
      next_offset: null,
      previous_offset: null,
      entry_index: 1,
      exit_index: 3,
    },
  }
}
