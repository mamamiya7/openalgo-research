import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { AnalysisChart, PortfolioResult } from '@/api/portfolioResearch'
import { ReportDrawdowns } from './ReportDrawdowns'
import { ReportInvestigation } from './ReportInvestigation'
import {
  availableMonthSelections,
  type DrawdownRow,
  type InvestigationSelection,
  investigationContext,
  investigationDate,
  monthSelectionFromPoint,
  savedDate,
} from './reportInvestigationEvidence'

vi.mock('./ResearchPlot', () => ({
  default: ({ chart }: { chart: AnalysisChart }) => (
    <output aria-label={`Plot ${chart.id}`}>{JSON.stringify(chart.figure)}</output>
  ),
}))

const drawdown = (): DrawdownRow => ({
  id: 1,
  peak_at: '2026-01-30',
  start_at: '2026-02-02',
  trough_at: '2026-02-03',
  end_at: '2026-02-05',
  recovered_at: '2026-02-05',
  status: 'recovered',
  depth_pct: 10,
  peak_equity: 100,
  trough_equity: 90,
  underwater_bars: 3,
  underwater_sessions: 3,
  recovery_sessions: 2,
  recovery_days: 2,
  peak_index: 0,
  trough_index: 2,
  end_index: 3,
})
const fixture = (): PortfolioResult => ({
  config: { initial_capital: 100 },
  strategies: [],
  per_strategy: [],
  summary: { net_return_pct: 99 },
  equity_curve: [
    '2026-01-30',
    '2026-02-02',
    '2026-02-03',
    '2026-02-05',
    '2026-02-27',
    '2026-03-02',
  ].map((date, index) => ({
    date,
    equity: [100, 95, 90, 100, 102, 103][index],
    cash: 20,
    drawdown_pct: [0, 5, 10, 0, 0, 0][index],
  })),
  ledger: [
    {
      symbol: 'CROSS_IN',
      strategy_name: 'Signal A',
      status: 'closed',
      entry_date: '2026-01-20',
      exit_date: '2026-02-02',
      pnl: 12,
    },
    {
      symbol: 'CROSS_OUT',
      strategy_name: 'Signal A',
      status: 'closed',
      entry_date: '2026-02-20',
      exit_date: '2026-03-02',
      pnl: 888,
    },
    { symbol: 'OPEN', status: 'pending', quantity: 2, entry_date: '2026-01-22', pnl: null },
    {
      symbol: 'BEFORE',
      status: 'closed',
      entry_date: '2026-01-20',
      exit_date: '2026-01-30',
      pnl: 777,
    },
    {
      symbol: 'AFTER',
      status: 'closed',
      entry_date: '2026-03-01',
      exit_date: '2026-03-02',
      pnl: 666,
    },
    { symbol: 'REJECTED', status: 'rejected', entry_date: null },
    { symbol: 'ZERO', status: 'closed', entry_date: '2026-02-03', exit_date: '2026-02-03', pnl: 0 },
  ],
  analysis: {
    version: 'research-analysis-v2',
    metrics: {},
    unavailable: {},
    catalog: [],
    basis: [],
    charts: [
      {
        id: 'monthly-returns',
        title: 'Monthly returns',
        status: 'available',
        figure: {
          data: [
            {
              type: 'heatmap',
              x: ['01', '02', '03', '04'],
              y: ['2026'],
              z: [[0, 2.123456, 0.980392, null]],
            },
          ],
          layout: {},
        },
      },
      ...['account-equity', 'account-underwater'].map(
        (id): AnalysisChart => ({
          id,
          title: id,
          status: 'available',
          figure: {
            data: [
              {
                type: 'scatter',
                x: ['2026-01-30', '2026-02-03', '2026-03-02'],
                y: id === 'account-equity' ? [100, 90, 103] : [0, -10, 0],
              },
            ],
            layout: { yaxis: { title: id === 'account-equity' ? 'INR' : '% below peak' } },
          },
        })
      ),
    ],
    report_depth: {
      version: 'research-report-depth-v1',
      daily_return_quantiles: { status: 'available', rows: [] },
      rolling: {
        windows: [21],
        annual_sessions: 252,
        sampling: 'daily',
        volatility_ddof: 1,
        risk_free_return: 0,
        required_return: 0,
        sortino_definition: 'saved',
      },
      drawdowns: {
        status: 'available',
        total: 1,
        shown: 1,
        truncated: false,
        basis: 'Saved curve',
        duration_definition: 'Saved underwater marks.',
        recovery_definition: 'Saved recovery.',
        rows: [drawdown()],
      },
    },
  },
})

describe('saved report investigation evidence', () => {
  it('resolves native heatmap coordinates using saved cells and actual marks, rejecting empty and malformed clicks', () => {
    const value = fixture()
    expect(monthSelectionFromPoint(value, { x: '02', y: '2026', z: 2.123456 })).toEqual({
      kind: 'month',
      month: '2026-02',
    })
    expect(monthSelectionFromPoint(value, { x: 1, y: 2026, z: 0 })).toEqual({
      kind: 'month',
      month: '2026-01',
    })
    for (const point of [
      { x: '04', y: '2026', z: null },
      { x: 'Feb', y: '2026' },
      { x: 0, y: 0 },
      { x: '02', y: '2026', z: 99 },
      { x: '2.1', y: '2026' },
      {},
    ])
      expect(monthSelectionFromPoint(value, point)).toBeNull()
    value.equity_curve = value.equity_curve.filter((row) => !row.date.startsWith('2026-02'))
    expect(monthSelectionFromPoint(value, { x: '02', y: '2026' })).toBeNull()
    expect(availableMonthSelections(value)).toEqual([
      { kind: 'month', month: '2026-01' },
      { kind: 'month', month: '2026-03' },
    ])
  })

  it('preserves the native monthly return, includes crossing/open trades, and sums only closures in the month', () => {
    const value = fixture()
    const before = JSON.stringify(value)
    const context = investigationContext(value, { kind: 'month', month: '2026-02' })!
    expect(context.monthlyReturn).toBe(2.123456)
    expect(context.trades.map((row) => row.row.symbol)).toEqual([
      'CROSS_IN',
      'CROSS_OUT',
      'OPEN',
      'ZERO',
    ])
    expect(context.closed).toBe(2)
    expect(context.open).toBe(2)
    expect(context.realizedPnl).toBe(12)
    expect(context.trades.find((row) => row.row.symbol === 'CROSS_OUT')).toMatchObject({
      status: 'Open at window end',
      realizedPnl: null,
    })
    expect(context.partial).toBe(false)
    expect(context.charts[0].figure?.data).toEqual(value.analysis!.charts[1].figure!.data)
    expect(context.charts[0].figure?.layout.shapes).toEqual([
      expect.objectContaining({ x0: '2026-02-01', x1: '2026-03-01' }),
    ])
    expect(JSON.stringify(value)).toBe(before)
  })

  it('includes funded native pending positions but excludes waiting signals for both engines', () => {
    const value = fixture()
    value.ledger = [
      {
        symbol: 'VBT_OPEN',
        status: 'pending',
        quantity: 3,
        entry_date: '2026-01-30',
        entry_price: 10,
        exit_date: null,
        pnl: null,
        unrealized_pnl: 4,
        reason: 'Position open at snapshot end',
      },
      {
        symbol: 'NAUTILUS_OPEN',
        status: 'pending',
        quantity: 2,
        entry_date: '2026-02-03',
        entry_price: 20,
        exit_date: null,
        pnl: null,
        reason: 'Position open at snapshot end',
      },
      {
        symbol: 'WAITING',
        status: 'pending',
        quantity: 0,
        entry_date: null,
        exit_date: null,
        pnl: null,
        reason: 'Next eligible opening is outside the snapshot',
      },
      { symbol: 'ZERO_QTY', status: 'pending', quantity: 0, entry_date: '2026-02-03', pnl: null },
    ]
    const before = JSON.stringify(value)
    const context = investigationContext(value, { kind: 'month', month: '2026-02' })!
    expect(context.trades.map((trade) => trade.row.symbol)).toEqual(['VBT_OPEN', 'NAUTILUS_OPEN'])
    expect(context.open).toBe(2)
    expect(context.closed).toBe(0)
    expect(context.realizedPnl).toBe(0)
    expect(context.skippedTrades).toBe(0)
    expect(
      context.trades.every(
        (trade) => trade.status === 'Open at window end' && trade.realizedPnl === null
      )
    ).toBe(true)
    expect(JSON.stringify(value)).toBe(before)
  })

  it('marks incomplete boundaries as partial and does not manufacture statistics from unavailable evidence', () => {
    const value = fixture()
    expect(investigationContext(value, { kind: 'month', month: '2026-01' })!.partial).toBe(true)
    expect(investigationContext(value, { kind: 'month', month: '2026-03' })!.partial).toBe(true)
    expect(investigationContext(value, { kind: 'month', month: '2026-04' })).toBeNull()
    value.ledger[0].pnl = null
    expect(investigationContext(value, { kind: 'month', month: '2026-02' })!.realizedPnl).toBeNull()
  })

  it('honors IST month boundaries for UTC and naive saved timestamps independently of the browser zone', () => {
    expect(savedDate('2026-01-31T20:00:00Z')!.day).toBe('2026-02-01')
    expect(savedDate('2026-02-01T01:30:00')!.start).toBe(savedDate('2026-01-31T20:00:00Z')!.start)
    expect(investigationDate('2026-01-31T20:00:00Z')).toBe('1 Feb 2026, 01:30:00 IST')
    for (const invalid of [
      '2026-02-30',
      '2026-13-01',
      '2026-02-01T25:00:00',
      '2026-02-01T10:00:00+25:00',
      'yesterday',
    ])
      expect(savedDate(invalid)).toBeNull()
    const value = fixture()
    value.ledger = [
      {
        status: 'closed',
        symbol: 'IN',
        entry_timestamp: '2026-01-31T18:20:00Z',
        exit_timestamp: '2026-01-31T18:30:00Z',
        pnl: 10,
      },
      {
        status: 'closed',
        symbol: 'OUT',
        entry_timestamp: '2026-01-31T18:00:00Z',
        exit_timestamp: '2026-01-31T18:29:59Z',
        pnl: 900,
      },
    ]
    expect(investigationContext(value, { kind: 'month', month: '2026-02' })!.realizedPnl).toBe(10)
  })

  it('uses exact saved intraday drawdown boundaries and treats coarse trade dates as uncertain', () => {
    const value = fixture()
    const row = {
      ...drawdown(),
      peak_at: '2026-02-03T09:15:00+05:30',
      start_at: '2026-02-03T09:16:00+05:30',
      trough_at: '2026-02-03T09:20:00+05:30',
      end_at: '2026-02-03T09:30:00+05:30',
      recovered_at: '2026-02-03T09:30:00+05:30',
    }
    value.analysis!.report_depth!.drawdowns.rows = [row]
    value.equity_curve = [
      { date: '2026-02-03', timestamp: row.start_at, equity: 95, cash: 20, drawdown_pct: 5 },
    ]
    value.ledger = [
      {
        status: 'closed',
        symbol: 'AT_END',
        entry_timestamp: '2026-02-03T09:10:00+05:30',
        exit_timestamp: '2026-02-03T04:00:00Z',
        pnl: 7,
      },
      {
        status: 'closed',
        symbol: 'AFTER_END',
        entry_timestamp: '2026-02-03T09:20:00+05:30',
        exit_timestamp: '2026-02-03T09:30:01+05:30',
        pnl: 900,
      },
      {
        status: 'closed',
        symbol: 'BEFORE',
        entry_timestamp: '2026-02-03T09:00:00+05:30',
        exit_timestamp: '2026-02-03T09:14:59+05:30',
        pnl: 800,
      },
      {
        status: 'closed',
        symbol: 'DATE_ONLY',
        entry_date: '2026-02-02',
        exit_date: '2026-02-03',
        pnl: 600,
      },
      { status: 'open', symbol: 'DATE_OPEN', entry_date: '2026-02-03' },
    ]
    const context = investigationContext(value, { kind: 'drawdown', row })!
    expect(context.from).toBe(row.peak_at)
    expect(context.to).toBe(row.end_at)
    expect(context.realizedPnl).toBe(7)
    expect(context.open).toBe(1)
    expect(context.timingUnknown).toBe(2)
    expect(context.trades).toHaveLength(4)
    expect(context.charts[0].figure?.layout.shapes).toEqual([
      expect.objectContaining({ x0: row.peak_at, x1: row.end_at }),
    ])
  })

  it.each([
    'vectorbt',
    'nautilus',
  ] as const)('uses the completed minute bar for %s close fills without admitting next-bar openings', (engine) => {
    const value = fixture()
    value.execution = { engine, interval: '1m' }
    const row = {
      ...drawdown(),
      peak_at: '2026-02-03T09:15:00+05:30',
      start_at: '2026-02-03T09:16:00+05:30',
      trough_at: '2026-02-03T09:20:00+05:30',
      end_at: '2026-02-03T09:30:00+05:30',
      recovered_at: '2026-02-03T09:30:00+05:30',
    }
    value.analysis!.report_depth!.drawdowns.rows = [row]
    value.equity_curve = [
      { date: '2026-02-03', timestamp: row.end_at, equity: 100, cash: 20, drawdown_pct: 0 },
    ]
    const entered = {
      status: 'closed',
      quantity: 1,
      entry_date: '2026-02-03',
      entry_timestamp: '2026-02-03T09:10:00+05:30',
      exit_date: '2026-02-03',
    }
    value.ledger = [
      {
        ...entered,
        symbol: 'ENDING_CLOSE',
        exit_timestamp: '2026-02-03T09:31:00+05:30',
        exit_timing: 'close',
        pnl: 7,
      },
      {
        ...entered,
        symbol: 'NEXT_OPEN_EXIT',
        exit_timestamp: '2026-02-03T09:31:00+05:30',
        exit_timing: 'open',
        pnl: 900,
      },
      {
        symbol: 'NEXT_OPEN_ENTRY',
        status: 'pending',
        quantity: 1,
        entry_date: '2026-02-03',
        entry_timestamp: '2026-02-03T09:31:00+05:30',
        pnl: null,
      },
      {
        ...entered,
        symbol: 'PEAK_BAR_CLOSE',
        exit_timestamp: '2026-02-03T09:16:00+05:30',
        exit_timing: 'close',
        pnl: 3,
      },
      {
        ...entered,
        symbol: 'PREVIOUS_BAR_CLOSE',
        exit_timestamp: '2026-02-03T09:15:00+05:30',
        exit_timing: 'close',
        pnl: 800,
      },
      {
        ...entered,
        symbol: 'UNKNOWN_PHASE',
        exit_timestamp: '2026-02-03T09:31:00+05:30',
        pnl: 600,
      },
    ]
    const before = JSON.stringify(value)
    const context = investigationContext(value, { kind: 'drawdown', row })!
    expect(context.trades.map((trade) => trade.row.symbol)).toEqual([
      'ENDING_CLOSE',
      'NEXT_OPEN_EXIT',
      'PEAK_BAR_CLOSE',
      'UNKNOWN_PHASE',
    ])
    expect(context.closed).toBe(2)
    expect(context.open).toBe(1)
    expect(context.timingUnknown).toBe(1)
    expect(context.realizedPnl).toBe(10)
    expect(context.trades[0].exit).toBe('2026-02-03T09:31:00+05:30')
    expect(context.trades[1].status).toBe('Open at window end')
    expect(context.trades[3].status).toBe('Timing unavailable')
    expect(context.minuteBars).toBe(true)
    expect(JSON.stringify(value)).toBe(before)
  })

  it('retains an ongoing initial-capital drawdown without inventing a peak timestamp or recovery', () => {
    const value = fixture()
    const row = {
      ...drawdown(),
      peak_at: null,
      peak_index: null,
      end_at: '2026-03-02',
      recovered_at: null,
      status: 'ongoing' as const,
      recovery_days: null,
      recovery_sessions: null,
    }
    value.analysis!.report_depth!.drawdowns.rows = [row]
    const context = investigationContext(value, { kind: 'drawdown', row })!
    expect(context.startingCapitalPeak).toBe(true)
    expect(context.from).toBe(row.start_at)
    expect(context.drawdown?.recovered_at).toBeNull()
    expect(
      investigationContext(value, { kind: 'drawdown', row: { ...row, end_at: '2027-01-01' } })
    ).toBeNull()
    value.ledger = [
      { status: 'closed', symbol: 'BROKEN', entry_date: 'bad', exit_date: '2026-02-03', pnl: 700 },
    ]
    expect(investigationContext(value, { kind: 'drawdown', row })!.skippedTrades).toBe(1)
  })
})

function Harness({
  result,
  onTrades,
}: {
  result: PortfolioResult
  onTrades?: (symbol?: string) => void
}) {
  const [selection, setSelection] = useState<InvestigationSelection | null>(null)
  return (
    <>
      <button type="button" onClick={() => setSelection({ kind: 'month', month: '2026-02' })}>
        Inspect February
      </button>
      <ReportInvestigation
        result={result}
        selection={selection}
        onClose={() => setSelection(null)}
        onTrades={onTrades}
      />
    </>
  )
}

describe('report investigation dialog', () => {
  it('provides an accessible contextual view, keeps whole-report values unchanged, and restores focus', async () => {
    const value = fixture()
    render(<Harness result={value} />)
    const trigger = screen.getByRole('button', { name: 'Inspect February' })
    await userEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'February 2026' })
    expect(within(dialog).getByText('Closed-trade P&L')).toBeVisible()
    expect(within(dialog).getByText(/not the account’s return/)).toBeVisible()
    expect(within(dialog).getByText('2 open at window end')).toBeVisible()
    await screen.findByLabelText('Plot account-equity-investigate-2026-02')
    await act(async () => expect((await axe(dialog)).violations).toEqual([]))
    expect(value.summary.net_return_pct).toBe(99)
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('caps the visible trade rows and sends an explicit all-symbol-trades action', async () => {
    const value = fixture()
    value.ledger = Array.from({ length: 45 }, (_, index) => ({
      ...value.ledger[0],
      symbol: `SYMBOL${index}`,
    }))
    const onTrades = vi.fn()
    render(<Harness result={value} onTrades={onTrades} />)
    await userEvent.click(screen.getByRole('button', { name: 'Inspect February' }))
    expect(screen.getAllByRole('row')).toHaveLength(21)
    expect(screen.getByText('1–20 of 45')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('21–40 of 45')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'All trades for SYMBOL20' }))
    expect(onTrades).toHaveBeenCalledWith('SYMBOL20')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('handles no trades and allows controlled drawdown expansion with keyboard inspection', async () => {
    const value = fixture()
    value.ledger = []
    render(<Harness result={value} />)
    await userEvent.click(screen.getByRole('button', { name: 'Inspect February' }))
    expect(screen.getByText('No recorded trades overlap this window.')).toBeVisible()
    await userEvent.keyboard('{Escape}')
    const drawdowns = value.analysis!.report_depth!.drawdowns
    drawdowns.rows = Array.from({ length: 7 }, (_, index) => ({ ...drawdown(), id: index + 1 }))
    const onInspect = vi.fn(),
      onExpandedChange = vi.fn()
    const { rerender } = render(
      <ReportDrawdowns
        drawdowns={drawdowns}
        expanded={false}
        onExpandedChange={onExpandedChange}
        onInspect={onInspect}
      />
    )
    expect(screen.getAllByRole('button', { name: /Inspect drawdown/ })).toHaveLength(5)
    await userEvent.click(screen.getByRole('button', { name: 'View all' }))
    expect(onExpandedChange).toHaveBeenCalledWith(true)
    rerender(
      <ReportDrawdowns
        drawdowns={drawdowns}
        expanded
        onExpandedChange={onExpandedChange}
        onInspect={onInspect}
      />
    )
    expect(screen.getAllByRole('button', { name: /Inspect drawdown/ })).toHaveLength(7)
    screen.getByRole('button', { name: 'Inspect drawdown 7' }).focus()
    await userEvent.keyboard('{Enter}')
    expect(onInspect).toHaveBeenCalledWith(drawdowns.rows[6])
  })
})
