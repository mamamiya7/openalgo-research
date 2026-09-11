import type { AnalysisChart, PortfolioAnalysis, PortfolioResult } from '@/api/portfolioResearch'

export type DrawdownRow = NonNullable<
  PortfolioAnalysis['report_depth']
>['drawdowns']['rows'][number]
export type InvestigationSelection =
  | { kind: 'month'; month: string }
  | { kind: 'drawdown'; row: DrawdownRow }

type SavedDate = { start: number; end: number; day: string; precise: boolean; stamp: string }
type LedgerRow = PortfolioResult['ledger'][number]
const DAY = 86_400_000
const IST = 330 * 60_000
const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)

// Date-only engine records describe a session, not an invented midnight fill.
// Naive saved timestamps follow the engine's IST contract, independent of the browser's zone.
export function savedDate(value: unknown): SavedDate | null {
  if (typeof value !== 'string') return null
  const match =
    /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2})(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})?)?$/.exec(
      value
    )
  if (!match) return null
  const [, year, month, day, hour, minute, second, fraction = '', offset] = match
  const base = Date.UTC(Number(year), Number(month) - 1, Number(day))
  if (new Date(base).toISOString().slice(0, 10) !== `${year}-${month}-${day}`) return null
  if (hour === undefined)
    return {
      start: base - IST,
      end: base - IST + DAY - 1,
      day: value,
      precise: false,
      stamp: value,
    }
  if (Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) return null
  if (offset && offset !== 'Z' && (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(4)) > 59))
    return null
  const normalized = `${year}-${month}-${day}T${hour}:${minute}:${second}${fraction}${offset ?? '+05:30'}`
  const instant = Date.parse(normalized)
  if (!Number.isFinite(instant)) return null
  return {
    start: instant,
    end: instant,
    day: new Date(instant + IST).toISOString().slice(0, 10),
    precise: true,
    stamp: value,
  }
}

export function investigationDate(value: string | null): string {
  if (value === null) return 'Starting capital'
  const parsed = savedDate(value)
  if (!parsed) return '—'
  if (!parsed.precise)
    return new Intl.DateTimeFormat('en-GB', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      timeZone: 'UTC',
    }).format(new Date(`${parsed.day}T00:00:00Z`))
  return `${new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZone: 'Asia/Kolkata' }).format(new Date(parsed.start))} IST`
}

export function monthLabel(month: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(`${month}-01T00:00:00Z`))
}

function monthValue(result: PortfolioResult, month: string): number | null {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) return null
  const chart = result.analysis?.charts.find(
    (item) => item.id === 'monthly-returns' && item.status === 'available'
  )
  const [year, number] = month.split('-')
  for (const trace of chart?.figure?.data ?? []) {
    if (
      trace.type !== 'heatmap' ||
      !Array.isArray(trace.x) ||
      !Array.isArray(trace.y) ||
      !Array.isArray(trace.z)
    )
      continue
    const column = trace.x.findIndex((x) => String(x).padStart(2, '0') === number)
    const row = trace.y.findIndex((y) => String(y) === year)
    const values = trace.z[row]
    const value = Array.isArray(values) ? values[column] : null
    if (finite(value)) return value
  }
  return null
}

export function monthSelectionFromPoint(
  result: PortfolioResult,
  point: { x?: unknown; y?: unknown; z?: unknown }
): InvestigationSelection | null {
  if (!/^(0?[1-9]|1[0-2])$/.test(String(point.x)) || !/^\d{4}$/.test(String(point.y))) return null
  const month = `${point.y}-${String(point.x).padStart(2, '0')}`
  const value = monthValue(result, month)
  if (value === null || (point.z !== undefined && point.z !== value)) return null
  if (
    !result.equity_curve.some((row) => savedDate(row.timestamp ?? row.date)?.day.startsWith(month))
  )
    return null
  return { kind: 'month', month }
}

export function availableMonthSelections(
  result: PortfolioResult
): Array<{ kind: 'month'; month: string }> {
  const months = new Set(
    result.equity_curve
      .map((row) => savedDate(row.timestamp ?? row.date)?.day.slice(0, 7))
      .filter((value): value is string => Boolean(value))
  )
  return [...months]
    .sort()
    .filter((month) => monthValue(result, month) !== null)
    .map((month) => ({ kind: 'month', month }))
}

export interface InvestigationTrade {
  index: number
  row: LedgerRow
  entry: string
  exit: string | null
  status: 'Closed in window' | 'Open at window end' | 'Timing unavailable'
  realizedPnl: number | null
  dateOnly: boolean
}

export interface InvestigationContext {
  key: string
  title: string
  from: string
  to: string
  partial: boolean
  startingCapitalPeak: boolean
  monthlyReturn: number | null
  drawdown: DrawdownRow | null
  trades: InvestigationTrade[]
  closed: number
  open: number
  timingUnknown: number
  realizedPnl: number | null
  skippedTrades: number
  minuteBars: boolean
  charts: AnalysisChart[]
}

function highlighted(
  chart: AnalysisChart,
  from: string,
  to: string,
  identity: string
): AnalysisChart {
  const figure = structuredClone(chart.figure!)
  // This changes only view annotations. Native trace values and account statistics stay intact.
  figure.layout = {
    ...figure.layout,
    shapes: [
      ...(Array.isArray(figure.layout.shapes) ? figure.layout.shapes : []),
      {
        type: 'rect',
        xref: 'x',
        yref: 'paper',
        x0: from,
        x1: to,
        y0: 0,
        y1: 1,
        fillcolor: 'rgba(59,130,246,0.16)',
        line: { width: 0 },
        layer: 'below',
      },
    ],
  }
  return { ...chart, id: `${chart.id}-investigate-${identity}`, figure }
}

export function investigationContext(
  result: PortfolioResult,
  selection: InvestigationSelection
): InvestigationContext | null {
  const marks = result.equity_curve.flatMap((point) => {
    const date = savedDate(point.timestamp ?? point.date)
    return date ? [date] : []
  })
  if (!marks.length) return null
  let start: number, end: number, from: string, to: string, title: string, key: string
  let partial = false
  let drawdown: DrawdownRow | null = null
  let monthlyReturn: number | null = null
  if (selection.kind === 'month') {
    monthlyReturn = monthValue(result, selection.month)
    const monthMarks = marks.filter((row) => row.day.startsWith(selection.month))
    if (monthlyReturn === null || !monthMarks.length) return null
    const [year, month] = selection.month.split('-').map(Number)
    start = Date.UTC(year, month - 1, 1) - IST
    end = Date.UTC(year, month, 1) - IST - 1
    from = monthMarks[0].stamp
    to = monthMarks[monthMarks.length - 1].stamp
    partial = !marks.some((row) => row.end < start) || !marks.some((row) => row.start > end)
    title = monthLabel(selection.month)
    key = selection.month
  } else {
    drawdown =
      result.analysis?.report_depth?.drawdowns.rows.find((row) => row.id === selection.row.id) ??
      null
    if (
      !drawdown ||
      drawdown.peak_at !== selection.row.peak_at ||
      drawdown.start_at !== selection.row.start_at ||
      drawdown.end_at !== selection.row.end_at ||
      drawdown.trough_at !== selection.row.trough_at
    )
      return null
    const peak = drawdown.peak_at ? savedDate(drawdown.peak_at) : null
    const first = savedDate(drawdown.start_at)
    const last = savedDate(drawdown.end_at)
    if (!first || !last || (drawdown.peak_at !== null && !peak)) return null
    from = drawdown.peak_at ?? drawdown.start_at
    to = drawdown.end_at
    start = (peak ?? first).start
    end = last.end
    if (end < start || !marks.some((row) => row.end >= start && row.start <= end)) return null
    title = `Drawdown ${drawdown.id}`
    key = `drawdown-${drawdown.id}`
  }
  const trades: InvestigationTrade[] = []
  const minuteBars = result.source?.interval === '1m' || result.execution?.interval === '1m'
  let skippedTrades = 0
  result.ledger.forEach((row, index) => {
    // Both native portfolio adapters keep funded open positions as `pending`.
    // Zero-quantity pending rows are waiting signals, not positions in this window.
    const enteredPending = row.status === 'pending' && finite(row.quantity) && row.quantity > 0
    if (row.status !== 'closed' && row.status !== 'open' && !enteredPending) return
    const entryStamp = row.entry_timestamp ?? row.entry_date
    const exitStamp = row.exit_timestamp ?? row.exit_date
    const entry = savedDate(entryStamp)
    const savedExit = row.status === 'closed' ? savedDate(exitStamp) : null
    // Native minute marks are keyed by bar open after processing the completed bar.
    // A known close fill is saved one minute later; membership uses its own bar,
    // while the visible fill timestamp remains the exact saved timestamp.
    const exit =
      minuteBars && savedExit?.precise && row.exit_timing === 'close'
        ? { ...savedExit, start: savedExit.start - 60_000, end: savedExit.end - 60_000 }
        : savedExit
    const unknownPhase =
      minuteBars &&
      savedExit?.precise &&
      !['open', 'intraday', 'close'].includes(String(row.exit_timing))
    if (!entry || (row.status === 'closed' && (!exit || exit.end < entry.start))) {
      skippedTrades += 1
      return
    }
    if (entry.start > end || (exit && exit.end < start)) return
    const closesWithin = !unknownPhase && exit !== null && exit.start >= start && exit.end <= end
    const openAtEnd = !unknownPhase && (exit === null || exit.start > end) && entry.end <= end
    trades.push({
      index,
      row,
      entry: String(entryStamp),
      exit: exit ? String(exitStamp) : null,
      status: closesWithin
        ? 'Closed in window'
        : openAtEnd
          ? 'Open at window end'
          : 'Timing unavailable',
      realizedPnl: closesWithin && finite(row.pnl) ? row.pnl : null,
      dateOnly: !entry.precise || Boolean(exit && !exit.precise),
    })
  })
  const closed = trades.filter((trade) => trade.status === 'Closed in window')
  const chartFrom = selection.kind === 'month' ? `${selection.month}-01` : from
  const chartTo =
    selection.kind === 'month' ? new Date(end + IST + 1).toISOString().slice(0, 10) : to
  return {
    key,
    title,
    from,
    to,
    partial,
    startingCapitalPeak: drawdown?.peak_at === null,
    monthlyReturn,
    drawdown,
    trades,
    closed: closed.length,
    open: trades.filter((trade) => trade.status === 'Open at window end').length,
    timingUnknown: trades.filter((trade) => trade.status === 'Timing unavailable').length,
    realizedPnl: closed.some((trade) => trade.realizedPnl === null)
      ? null
      : closed.reduce((sum, trade) => sum + trade.realizedPnl!, 0),
    skippedTrades,
    minuteBars,
    charts: ['account-equity', 'account-underwater'].flatMap((id) => {
      const chart = result.analysis?.charts.find(
        (item) => item.id === id && item.status === 'available' && item.figure
      )
      return chart ? [highlighted(chart, chartFrom, chartTo, key)] : []
    }),
  }
}
