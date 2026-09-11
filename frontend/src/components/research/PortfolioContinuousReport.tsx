import { Maximize2 } from 'lucide-react'
import { useId, useMemo, useRef, useState } from 'react'
import type { AnalysisChart, PortfolioResult } from '@/api/portfolioResearch'
import {
  defaultReportPreferences,
  type ReportExpansion,
  type ReportPreferences,
} from '@/api/reportPreferences'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { AnalysisFigure } from './AnalysisCharts'
import { AnalysisMetricTable } from './AnalysisMetricTable'
import {
  type AnalysisActions,
  AnalysisBasis,
  AnalysisPreparation,
  NativeRecords,
} from './PortfolioAnalysis'
import {
  availableReportMetrics,
  type ReportMetric,
  reportMetricDescription,
  reportMetricLabel,
  reportMetrics,
  reportMetricText,
} from './portfolioReportMetrics'
import { trialMetricText } from './portfolioTrialMetrics'
import { ReportCustomize } from './ReportCustomize'
import { ReportDrawdowns } from './ReportDrawdowns'
import {
  availableMonthSelections,
  type InvestigationSelection,
  monthSelectionFromPoint,
  ReportInvestigation,
} from './ReportInvestigation'
import { reportChartView as chartView } from './reportChartPresentation'
import { monthLabel } from './reportInvestigationEvidence'
import type { ReportPreferenceController } from './useReportPreferences'

const sections = ['Performance', 'Consistency', 'Risk', 'Trades', 'All statistics'] as const
const cash = (value: unknown) => trialMetricText(reportMetrics[1], value)
const number = (value: unknown, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`
    : '—'

function ReportChart({
  chart,
  height = 280,
  deferred = true,
  onPoint,
}: {
  chart: AnalysisChart
  height?: number
  deferred?: boolean
  onPoint?: (point: { x?: unknown; y?: unknown; z?: unknown }) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const pointAfterClose = useRef<{ x?: unknown; y?: unknown; z?: unknown } | null>(null)
  const opener = useRef<HTMLButtonElement>(null)
  if (chart.status !== 'available' || !chart.figure) return null
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-medium">{chart.title}</h4>
        <Dialog open={expanded} onOpenChange={setExpanded}>
          <DialogTrigger asChild>
            <Button
              ref={opener}
              size="icon"
              variant="ghost"
              className="size-7 text-muted-foreground"
              aria-label={`Expand ${chart.title}`}
            >
              <Maximize2 className="size-3.5" aria-hidden="true" />
            </Button>
          </DialogTrigger>
          <DialogContent
            className="sm:max-w-[90vw] motion-reduce:animate-none"
            onCloseAutoFocus={(event) => {
              const point = pointAfterClose.current
              pointAfterClose.current = null
              if (point && onPoint) {
                event.preventDefault()
                opener.current?.focus({ preventScroll: true })
                onPoint(point)
              }
            }}
          >
            <DialogHeader>
              <DialogTitle>{chart.title}</DialogTitle>
              <DialogDescription>Hover to inspect · Drag to zoom</DialogDescription>
            </DialogHeader>
            <AnalysisFigure
              chart={chart}
              onPoint={
                onPoint
                  ? (point) => {
                      pointAfterClose.current = point
                      setExpanded(false)
                    }
                  : undefined
              }
              height={Math.min(
                620,
                typeof window === 'undefined' ? 500 : window.innerHeight * 0.65
              )}
            />
          </DialogContent>
        </Dialog>
      </div>
      <AnalysisFigure chart={chart} height={height} deferred={deferred} onPoint={onPoint} />
    </div>
  )
}

function SummaryStatistics({
  result,
  metrics,
}: {
  result: PortfolioResult
  metrics: ReportMetric[]
}) {
  const groups = [...new Set(metrics.map((metric) => metric.group))]
  return (
    <section className="min-w-0 space-y-5 lg:border-l lg:pl-6" aria-label="Account statistics">
      {groups.map((group) => (
        <div key={group} className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {group}
          </h3>
          <dl className="space-y-2 text-sm">
            {metrics
              .filter((metric) => metric.group === group)
              .map((metric) => {
                return (
                  <div className="flex items-start justify-between gap-3" key={metric.key}>
                    <dt className="min-w-0 text-muted-foreground">
                      <details>
                        <summary className="cursor-pointer list-none underline-offset-4 hover:underline">
                          {reportMetricLabel(metric)}
                        </summary>
                        <p className="mt-1 max-w-56 text-xs leading-relaxed">
                          {reportMetricDescription(result, metric)}
                        </p>
                      </details>
                    </dt>
                    <dd className="whitespace-nowrap text-right tabular-nums">
                      {reportMetricText(result, metric)}
                    </dd>
                  </div>
                )
              })}
          </dl>
        </div>
      ))}
    </section>
  )
}

export function PortfolioContinuousReport({
  result,
  onTrades,
  onSettings,
  preferences,
  ...actions
}: AnalysisActions & {
  result: PortfolioResult
  onTrades: (strategyId?: string, symbol?: string) => void
  onSettings?: () => void
  preferences?: ReportPreferenceController
}) {
  const id = useId()
  const analysis = result.analysis
  const [investigation, setInvestigation] = useState<InvestigationSelection | null>(null)
  const months = useMemo(() => availableMonthSelections(result), [result])
  const [localPreferences, setLocalPreferences] = useState(defaultReportPreferences)
  const view = preferences?.value ?? localPreferences
  const controlsDisabled = preferences ? !preferences.ready || preferences.saving : false
  const savePreferences = async (changes: Partial<ReportPreferences>) => {
    if (preferences) return preferences.save(changes)
    setLocalPreferences((previous) => ({ ...previous, ...changes }))
    return true
  }
  const setExpanded = (key: ReportExpansion, expanded: boolean) => {
    if (view.expanded_sections.includes(key) === expanded || controlsDisabled) return
    void savePreferences({
      expanded_sections: expanded
        ? [...view.expanded_sections, key]
        : view.expanded_sections.filter((item) => item !== key),
    })
  }
  const scale = view.performance_view
  const log = view.log_equity
  const windowSize = view.rolling_window
  const showAll = view.expanded_sections.includes('all_statistics')
  const showAnnual = view.expanded_sections.includes('annual')
  const showPrices = view.expanded_sections.includes('prices')
  const metricChoices = availableReportMetrics(result)
  const requestedHeadlines = view.headline_metrics
    .map((key) => metricChoices.find((metric) => metric.key === key))
    .filter((metric): metric is ReportMetric => Boolean(metric))
  const headlines = requestedHeadlines.length ? requestedHeadlines : reportMetrics.slice(0, 6)
  const statisticMetrics = view.statistic_metrics
    .map((key) => metricChoices.find((metric) => metric.key === key))
    .filter((metric): metric is ReportMetric => Boolean(metric))
  const [priceSymbol, setPriceSymbol] = useState('')
  const symbols = analysis?.price_symbols ?? []
  const selectedSymbol = symbols.includes(priceSymbol)
    ? priceSymbol
    : (analysis?.price_symbol ?? symbols[0])
  const charts = useMemo(() => {
    const available = new Map(
      (analysis?.charts ?? [])
        .filter((chart) => chart.status === 'available' && chart.figure)
        .map((chart) => [chart.id, chart])
    )
    // Older evidence already has marked equity. Transform its coordinates only;
    // headline values and optimizer scores always remain their saved values.
    const curve = result.equity_curve
    const x = curve.map((point) => point.timestamp ?? point.date)
    const initial = result.summary.initial_capital
    const base = (
      key: string,
      title: string,
      values: (number | null)[],
      axis: string
    ): AnalysisChart => ({
      id: key,
      title,
      status: 'available',
      figure: {
        data: [{ type: 'scatter', mode: 'lines', x, y: values }],
        layout: { yaxis: { title: axis } },
      },
    })
    if (curve.length) {
      if (!available.has('account-cumulative') && typeof initial === 'number' && initial > 0)
        available.set(
          'account-cumulative',
          base(
            'account-cumulative',
            'Cumulative return',
            curve.map((point) => (point.equity / initial - 1) * 100),
            '%'
          )
        )
      if (!available.has('account-underwater'))
        available.set(
          'account-underwater',
          base(
            'account-underwater',
            'Underwater',
            curve.map((point) =>
              Number.isFinite(point.drawdown_pct) ? -point.drawdown_pct : null
            ),
            '% below peak'
          )
        )
      // An equity view contains account equity only. Cash has its own capital row.
      available.set(
        'report-equity',
        base(
          'report-equity',
          'Account equity',
          curve.map((point) => point.equity),
          'INR'
        )
      )
    }
    return available
  }, [analysis?.charts, result.equity_curve, result.summary.initial_capital])
  const cumulative = charts.get('account-cumulative')
  const equity = charts.get('report-equity')
  const performance = scale === 'return' && cumulative ? cumulative : equity
  const canLog =
    performance?.id === 'report-equity' &&
    result.equity_curve.length > 0 &&
    result.equity_curve.every((point) => point.equity > 0)
  const windows = [21, 63, 126].filter(
    (value) =>
      charts.has(value === 21 ? 'rolling-sharpe' : `rolling-sharpe-${value}`) ||
      charts.has(value === 21 ? 'rolling-volatility' : `rolling-volatility-${value}`)
  )
  const activeWindow = windows.includes(windowSize) ? windowSize : windows[0]
  const rolling = (name: string) =>
    charts.get(activeWindow === 21 ? name : `${name}-${activeWindow}`)
  const hasConsistency = charts.has('monthly-returns') || charts.has('daily-return-distribution')
  const hasRisk =
    windows.length > 0 ||
    charts.has('drawdown-episodes') ||
    Boolean(analysis?.report_depth?.drawdowns.rows.length)
  const visibleSections = sections
    .filter((section) => section !== 'Consistency' || hasConsistency)
    .filter((section) => section !== 'Risk' || hasRisk)
  const showChart = (key: string, height?: number) => {
    const chart = charts.get(key)
    return chart ? (
      <ReportChart
        chart={chartView(chart)}
        height={height}
        onPoint={
          key === 'monthly-returns'
            ? (point) => {
                const selection = monthSelectionFromPoint(result, point)
                if (selection) setInvestigation(selection)
              }
            : undefined
        }
      />
    ) : null
  }
  const target = (section: string) => `${id}-${section.toLowerCase().replaceAll(' ', '-')}`
  return (
    <section className="min-w-0 space-y-6" aria-label="Portfolio report">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {onSettings && (
          <Button size="sm" variant="ghost" onClick={onSettings}>
            Settings
          </Button>
        )}
        {preferences?.saving && (
          <output className="text-xs text-muted-foreground">Saving preferences…</output>
        )}
        {preferences?.error && (
          <span role="alert" className="text-xs text-destructive">
            {preferences.error}
          </span>
        )}
        {preferences?.error && (
          <Button
            size="sm"
            variant="ghost"
            disabled={preferences.saving}
            onClick={() => void preferences.reload()}
          >
            Reload preferences
          </Button>
        )}
        <ReportCustomize
          result={result}
          value={view}
          disabled={controlsDisabled}
          saving={preferences?.saving ?? false}
          onSave={savePreferences}
        />
      </div>
      <dl
        aria-label="Headline statistics"
        className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-3 xl:grid-cols-6"
      >
        {headlines.map((metric) => (
          <div key={metric.key} title={reportMetricDescription(result, metric)}>
            <dt className="mb-1 text-xs text-muted-foreground">{reportMetricLabel(metric)}</dt>
            <dd className="text-xl font-semibold tabular-nums tracking-tight">
              {reportMetricText(result, metric)}
            </dd>
          </div>
        ))}
      </dl>
      <nav
        aria-label="Report sections"
        className="sticky top-0 z-10 flex max-w-full gap-4 overflow-x-auto border-b bg-background/95 py-3 text-xs backdrop-blur-sm sm:gap-6"
      >
        {visibleSections.map((section) => (
          <a
            key={section}
            href={`#${target(section)}`}
            className="whitespace-nowrap text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:underline"
            onClick={(event) => {
              event.preventDefault()
              if (section === 'All statistics') setExpanded('all_statistics', true)
              document
                .getElementById(target(section))
                ?.scrollIntoView({ block: 'start', behavior: 'instant' })
            }}
          >
            {section}
          </a>
        ))}
      </nav>
      <section
        id={target('Performance')}
        aria-label="Performance"
        className={`scroll-mt-16 grid min-w-0 gap-6 ${statisticMetrics.length ? 'lg:grid-cols-[minmax(0,2fr)_minmax(230px,1fr)]' : ''}`}
      >
        <div className="min-w-0 space-y-3">
          {performance && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <fieldset className="flex gap-1" aria-label="Performance view">
                  <Button
                    size="sm"
                    variant={scale === 'return' && cumulative ? 'secondary' : 'ghost'}
                    disabled={!cumulative || controlsDisabled}
                    onClick={() => void savePreferences({ performance_view: 'return' })}
                  >
                    Return %
                  </Button>
                  <Button
                    size="sm"
                    variant={scale === 'equity' || !cumulative ? 'secondary' : 'ghost'}
                    disabled={!equity || controlsDisabled}
                    onClick={() => void savePreferences({ performance_view: 'equity' })}
                  >
                    Equity
                  </Button>
                </fieldset>
                {performance.id === 'report-equity' && (
                  <label
                    className="flex items-center gap-2 text-xs text-muted-foreground"
                    title={
                      !canLog ? 'Log scale needs positive equity at every saved mark.' : undefined
                    }
                  >
                    <input
                      type="checkbox"
                      checked={log && canLog}
                      disabled={!canLog || controlsDisabled}
                      onChange={(event) =>
                        void savePreferences({ log_equity: event.target.checked })
                      }
                    />
                    Log scale
                  </label>
                )}
              </div>
              <ReportChart
                chart={chartView(performance, performance.id === 'report-equity' && log && canLog)}
                height={270}
                deferred={false}
              />
            </>
          )}
          {charts.get('account-underwater') && (
            <ReportChart
              chart={chartView(charts.get('account-underwater')!)}
              height={155}
              deferred={false}
            />
          )}
        </div>
        {statisticMetrics.length > 0 && (
          <SummaryStatistics result={result} metrics={statisticMetrics} />
        )}
      </section>
      <AnalysisPreparation {...actions} missing={!analysis} />
      {hasConsistency && (
        <section
          id={target('Consistency')}
          aria-labelledby={target('Consistency-heading')}
          className="scroll-mt-16 space-y-4 border-t pt-6"
        >
          <h3 id={target('Consistency-heading')} className="font-semibold">
            Consistency
          </h3>
          <div className="grid min-w-0 gap-6 lg:grid-cols-2">
            {showChart('monthly-returns', 240)}
            {showChart('daily-return-distribution', 240)}
          </div>
          {months.length > 0 && (
            <select
              className="h-8 max-w-full rounded-md border bg-background px-2 text-xs"
              aria-label="Inspect a month"
              value=""
              onChange={(event) => {
                const month = months.find((item) => item.month === event.target.value)
                if (month) setInvestigation(month)
              }}
            >
              <option value="">Inspect a month…</option>
              {months.map((selection) => (
                <option key={selection.month} value={selection.month}>
                  {monthLabel(selection.month)}
                </option>
              ))}
            </select>
          )}
          {(charts.has('yearly-returns') || charts.has('daily-return-quantiles')) && (
            <details
              open={showAnnual}
              onToggle={(event) => setExpanded('annual', event.currentTarget.open)}
            >
              <summary
                className="cursor-pointer py-2 text-sm text-muted-foreground"
                onClick={(event) => {
                  event.preventDefault()
                  setExpanded('annual', !showAnnual)
                }}
              >
                Annual returns and quantiles
              </summary>
              {showAnnual && (
                <div className="grid min-w-0 gap-6 pt-3 lg:grid-cols-2">
                  {showChart('yearly-returns')}
                  {showChart('daily-return-quantiles')}
                </div>
              )}
            </details>
          )}
        </section>
      )}
      {hasRisk && (
        <section
          id={target('Risk')}
          aria-labelledby={target('Risk-heading')}
          className="scroll-mt-16 space-y-4 border-t pt-6"
        >
          <div className="flex items-center justify-between gap-3">
            <h3 id={target('Risk-heading')} className="font-semibold">
              Risk
            </h3>
            {windows.length > 0 && (
              <select
                aria-label="Rolling window"
                className="h-8 rounded-md border bg-background px-2 text-xs"
                value={activeWindow}
                disabled={controlsDisabled}
                onChange={(event) =>
                  void savePreferences({
                    rolling_window: Number(event.target.value) as 21 | 63 | 126,
                  })
                }
              >
                {[21, 63, 126].map((value) => (
                  <option key={value} value={value} disabled={!windows.includes(value)}>
                    {value} sessions
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="grid min-w-0 gap-6 lg:grid-cols-2">
            {['rolling-sharpe', 'rolling-volatility'].map((key) => {
              const chart = rolling(key)
              return chart ? <ReportChart key={key} chart={chartView(chart)} /> : null
            })}
          </div>
          {analysis?.report_depth?.drawdowns.rows.length ? (
            <ReportDrawdowns
              drawdowns={analysis.report_depth.drawdowns}
              expanded={view.expanded_sections.includes('drawdowns')}
              onExpandedChange={(expanded) => setExpanded('drawdowns', expanded)}
              onInspect={(row) => setInvestigation({ kind: 'drawdown', row })}
            />
          ) : (
            showChart('drawdown-episodes', 280)
          )}
          {rolling('rolling-sortino') && (
            <details
              open={view.expanded_sections.includes('sortino')}
              onToggle={(event) => setExpanded('sortino', event.currentTarget.open)}
            >
              <summary
                className="cursor-pointer py-2 text-sm text-muted-foreground"
                onClick={(event) => {
                  event.preventDefault()
                  setExpanded('sortino', !view.expanded_sections.includes('sortino'))
                }}
              >
                Rolling Sortino
              </summary>
              <ReportChart chart={chartView(rolling('rolling-sortino')!)} />
            </details>
          )}
        </section>
      )}
      <section
        id={target('Trades')}
        aria-labelledby={target('Trades-heading')}
        className="scroll-mt-16 space-y-4 border-t pt-6"
      >
        <div className="flex items-center justify-between gap-3">
          <h3 id={target('Trades-heading')} className="font-semibold">
            Trades and capital
          </h3>
          <Button variant="ghost" size="sm" onClick={() => onTrades()}>
            View trades
          </Button>
        </div>
        <div className="grid min-w-0 gap-6 lg:grid-cols-2">
          {showChart('trade-pnl')}
          {showChart('trade-duration')}
        </div>
        {showChart('account-exposure', 230)}
        <dl className="flex flex-wrap gap-x-10 gap-y-3 text-sm">
          {reportMetrics
            .filter((metric) => metric.group === 'Capital')
            .map((metric) => (
              <div key={metric.key}>
                <dt className="text-xs text-muted-foreground">{metric.label}</dt>
                <dd className="mt-1 tabular-nums">{reportMetricText(result, metric)}</dd>
              </div>
            ))}
        </dl>
        {result.per_strategy.length > 0 && (
          <div className="space-y-3">
            <h4 className="text-sm font-medium">Strategy contributions</h4>
            <div className="max-w-full overflow-x-auto rounded-lg border">
              <table className="w-full text-sm">
                <caption className="sr-only">Strategy contributions to the shared account</caption>
                <thead className="bg-muted/40">
                  <tr>
                    {[
                      'Strategy',
                      'Allocation',
                      'Net P&L',
                      'Return contribution',
                      'Closed trades',
                    ].map((label) => (
                      <th
                        key={label}
                        scope="col"
                        className="whitespace-nowrap p-3 text-left text-xs font-medium text-muted-foreground"
                      >
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {result.per_strategy.map((strategy) => (
                    <tr key={strategy.id}>
                      <th scope="row" className="p-3 text-left font-medium">
                        <button
                          type="button"
                          className="underline-offset-4 hover:underline focus-visible:underline"
                          onClick={() => onTrades(strategy.id)}
                        >
                          {strategy.name}
                        </button>
                      </th>
                      <td className="whitespace-nowrap p-3 tabular-nums">
                        {number(strategy.allocation_pct, '%')}
                      </td>
                      <td className="whitespace-nowrap p-3 tabular-nums">
                        {cash(strategy.net_pnl)}
                      </td>
                      <td
                        className="whitespace-nowrap p-3 tabular-nums"
                        title="Percentage points of the portfolio return"
                      >
                        {number(strategy.contribution_pct, ' pp')}
                      </td>
                      <td className="whitespace-nowrap p-3 tabular-nums">
                        {number(strategy.summary.closed_trades)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {charts.has('bars-with-fills') && (
          <details
            open={showPrices}
            onToggle={(event) => setExpanded('prices', event.currentTarget.open)}
          >
            <summary
              className="cursor-pointer py-2 text-sm text-muted-foreground"
              onClick={(event) => {
                event.preventDefault()
                setExpanded('prices', !showPrices)
              }}
            >
              Prices and fills
            </summary>
            {showPrices && (
              <div className="space-y-3 pt-3">
                {symbols.length > 1 && !actions.readOnly && (
                  <div className="flex gap-3">
                    <select
                      aria-label="Price chart symbol"
                      className="h-9 max-w-full rounded-md border bg-background px-3 text-sm"
                      value={selectedSymbol}
                      disabled={actions.busy}
                      onChange={(event) => setPriceSymbol(event.target.value)}
                    >
                      {symbols.map((symbol) => (
                        <option key={symbol} value={symbol}>
                          {symbol}
                        </option>
                      ))}
                    </select>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={actions.busy}
                      onClick={() => actions.onPrepare(undefined, selectedSymbol)}
                    >
                      Show prices
                    </Button>
                  </div>
                )}
                {showChart('bars-with-fills', 420)}
              </div>
            )}
          </details>
        )}
      </section>
      <section id={target('All statistics')} className="scroll-mt-16 border-t pt-4">
        {analysis &&
          analysis.version !== 'research-analysis-v2' &&
          !actions.busy &&
          !actions.readOnly && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mb-3"
              onClick={() => actions.onPrepare()}
            >
              Update report
            </Button>
          )}
        <details
          open={showAll}
          onToggle={(event) => setExpanded('all_statistics', event.currentTarget.open)}
        >
          <summary
            className="cursor-pointer py-2 font-semibold"
            onClick={(event) => {
              event.preventDefault()
              setExpanded('all_statistics', !showAll)
            }}
          >
            All statistics
          </summary>
          {showAll && analysis && (
            <div className="space-y-4 pt-3">
              <AnalysisMetricTable catalog={analysis.catalog} analysis={analysis} />
              <AnalysisBasis analysis={analysis} />
              {actions.response.status === 'complete' && (
                <Button asChild variant="ghost" size="sm">
                  <a href={actions.exportUrl} download>
                    Export analysis
                  </a>
                </Button>
              )}
            </div>
          )}
          {showAll && !analysis && (
            <p className="py-3 text-sm text-muted-foreground">
              Detailed statistics have not been saved for this result.
            </p>
          )}
        </details>
        {result.engine_records && (
          <NativeRecords
            records={result.engine_records}
            expanded={view.expanded_sections.includes('engine_records')}
            onExpandedChange={(expanded) => setExpanded('engine_records', expanded)}
          />
        )}
      </section>
      <ReportInvestigation
        result={result}
        selection={investigation}
        onClose={() => setInvestigation(null)}
        onTrades={(symbol) => onTrades(undefined, symbol)}
      />
    </section>
  )
}
