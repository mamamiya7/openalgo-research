import { useMemo, useRef, useState } from 'react'
import type { PortfolioResult } from '@/api/portfolioResearch'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { AnalysisFigure } from './AnalysisCharts'
import { reportMoney, useReportCurrency } from './ReportCurrency'
import { reportChartView } from './reportChartPresentation'
import {
  type InvestigationContext,
  type InvestigationSelection,
  investigationContext,
  investigationDate,
} from './reportInvestigationEvidence'

export type { DrawdownRow, InvestigationSelection } from './reportInvestigationEvidence'
export { availableMonthSelections, monthSelectionFromPoint } from './reportInvestigationEvidence'

const PAGE_SIZE = 20
const number = (value: number | null, suffix = '') =>
  value === null ? '—' : `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`

export function ReportInvestigation({
  result,
  selection,
  onClose,
  onTrades,
}: {
  result: PortfolioResult
  selection: InvestigationSelection | null
  onClose: () => void
  onTrades?: (symbol?: string) => void
}) {
  const context = useMemo(
    () => (selection ? investigationContext(result, selection) : null),
    [result, selection]
  )
  const title = useRef<HTMLHeadingElement>(null)
  const trigger = useRef<HTMLElement | null>(null)
  return (
    <Dialog
      open={selection !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent
        className="max-h-[90dvh] overflow-y-auto p-4 sm:max-w-5xl sm:p-6 motion-reduce:animate-none"
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          trigger.current =
            document.activeElement instanceof HTMLElement ? document.activeElement : null
          title.current?.focus({ preventScroll: true })
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          if (trigger.current?.isConnected) trigger.current.focus({ preventScroll: true })
        }}
      >
        <DialogHeader>
          <DialogTitle ref={title} tabIndex={-1} className="pr-6 leading-snug outline-none">
            {context?.title ?? 'Saved period'}
          </DialogTitle>
          <DialogDescription>
            {context
              ? `${context.monthlyReturn !== null ? 'Saved marks · ' : ''}${investigationDate(context.from)} – ${investigationDate(context.to)}${context.partial ? ' · Partial period' : ''}`
              : 'This selection has no matching saved evidence.'}
          </DialogDescription>
        </DialogHeader>
        {context && (
          <InvestigationBody
            key={context.key}
            context={context}
            onTrades={
              onTrades
                ? (symbol) => {
                    onClose()
                    onTrades(symbol)
                  }
                : undefined
            }
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function InvestigationBody({
  context,
  onTrades,
}: {
  context: InvestigationContext
  onTrades?: (symbol?: string) => void
}) {
  const [page, setPage] = useState(0)
  const currency = useReportCurrency()
  const money = (value: number | null) => reportMoney(value, currency)
  const rows = context.trades.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  return (
    <div className="min-w-0 space-y-6">
      <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4" aria-label="Window statistics">
        <div>
          <dt className="text-xs text-muted-foreground">
            {context.drawdown ? 'Saved depth' : 'Saved monthly return'}
          </dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums">
            {number(context.drawdown?.depth_pct ?? context.monthlyReturn, '%')}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Trades overlapping</dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums">{context.trades.length}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Closed in window</dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums">{context.closed}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Closed-trade P&amp;L</dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums">{money(context.realizedPnl)}</dd>
        </div>
      </dl>
      <p className="text-xs text-muted-foreground">
        Closed-trade P&amp;L includes each trade’s full realized result when it closes in this
        window. It is not the account’s return for the window.
      </p>
      {context.drawdown && (
        <p className="text-sm text-muted-foreground">
          {context.drawdown.recovered_at
            ? `Recovered · ${investigationDate(context.drawdown.recovered_at)}`
            : 'Ongoing at the final saved mark'}
          {context.startingCapitalPeak ? ' · Peak: starting capital' : ''}
        </p>
      )}
      {context.charts.length > 0 && (
        <section className="min-w-0 space-y-3" aria-label="Highlighted saved account charts">
          <p className="text-xs text-muted-foreground">
            The shaded window sits within the full saved account history.
          </p>
          {context.charts
            .map((saved) => reportChartView(saved))
            .map((chart) => (
              <div key={chart.id} className="min-w-0 rounded-lg border p-2 sm:p-3">
                <h3 className="px-2 text-sm font-medium">{chart.title}</h3>
                <AnalysisFigure
                  chart={chart}
                  height={chart.id.startsWith('account-underwater') ? 200 : 260}
                />
              </div>
            ))}
        </section>
      )}
      <section className="min-w-0 space-y-3" aria-label="Trades overlapping the selected window">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-sm font-medium">Trades in this window</h3>
          <span className="text-xs text-muted-foreground">{context.open} open at window end</span>
        </div>
        {rows.length ? (
          <div className="max-w-full overflow-x-auto rounded-lg border">
            <table className="w-full text-xs">
              <caption className="sr-only">
                Saved trades overlapping the selected window; closed P&amp;L only for closures
                inside it
              </caption>
              <thead className="bg-muted">
                <tr>
                  {['Symbol', 'Strategy', 'Entry', 'Exit', 'Window status', 'Closed P&L'].map(
                    (label) => (
                      <th
                        key={label}
                        scope="col"
                        className="whitespace-nowrap p-3 text-left font-medium text-muted-foreground"
                      >
                        {label}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((trade) => (
                  <tr key={trade.index}>
                    <td className="p-3">
                      {onTrades && typeof trade.row.symbol === 'string' ? (
                        <Button
                          type="button"
                          variant="link"
                          className="h-auto p-0 text-xs"
                          aria-label={`All trades for ${trade.row.symbol}`}
                          onClick={() => onTrades(trade.row.symbol as string)}
                        >
                          {trade.row.symbol}
                        </Button>
                      ) : (
                        (trade.row.symbol ?? '—')
                      )}
                    </td>
                    <td className="p-3">
                      {trade.row.strategy_name ?? trade.row.strategy_id ?? '—'}
                    </td>
                    <td className="whitespace-nowrap p-3">{investigationDate(trade.entry)}</td>
                    <td className="whitespace-nowrap p-3">
                      {trade.exit ? investigationDate(trade.exit) : 'Open'}
                    </td>
                    <td className="whitespace-nowrap p-3">{trade.status}</td>
                    <td className="whitespace-nowrap p-3 tabular-nums">
                      {money(trade.realizedPnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No recorded trades overlap this window.</p>
        )}
        {context.trades.length > PAGE_SIZE && (
          <nav
            aria-label="Window trades pages"
            className="flex items-center justify-between gap-3 text-xs"
          >
            <span>
              {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, context.trades.length)} of{' '}
              {context.trades.length}
            </span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                Previous
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={(page + 1) * PAGE_SIZE >= context.trades.length}
                onClick={() => setPage(page + 1)}
              >
                Next
              </Button>
            </div>
          </nav>
        )}
      </section>
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer py-1">Window details</summary>
        <div className="mt-2 space-y-2 leading-relaxed">
          <p>
            Account marks, monthly returns and drawdown values are saved report evidence. Inspecting
            this window does not rerun the strategy or change the whole-period statistics.
          </p>
          {context.partial && (
            <p>
              This month has no saved account marks on both sides of its boundaries, so it is shown
              as a partial period.
            </p>
          )}
          {context.startingCapitalPeak && (
            <p>
              The initial capital peak has no recorded timestamp. Shading begins at the first
              underwater mark.
            </p>
          )}
          <p>
            Trades can begin before or end after the window. Open positions have no closed P&amp;L
            here. All times are IST; daily records have no assumed fill time.
          </p>
          <p>
            Trades overlap the selected saved bars, including a drawdown’s peak bar. Their P&amp;L
            is not attribution of the drawdown loss.
          </p>
          {context.minuteBars && (
            <p>
              Minute account marks use the bar’s opening time after the bar completes. Known close
              fills belong to that completed bar; the table keeps their saved fill time.
            </p>
          )}
          {context.timingUnknown > 0 && (
            <p>
              {context.timingUnknown} trades have incomplete timing for this window; their P&amp;L
              is excluded from this window’s total.
            </p>
          )}
          {context.skippedTrades > 0 && (
            <p>{context.skippedTrades} trades lack usable saved entry or exit dates.</p>
          )}
        </div>
      </details>
    </div>
  )
}
