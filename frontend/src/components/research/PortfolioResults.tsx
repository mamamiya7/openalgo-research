import { MoreHorizontal } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import {
  type PortfolioJob,
  type PortfolioResult,
  type PortfolioSettings,
  portfolioResearch,
} from '@/api/portfolioResearch'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuthStore } from '@/stores/authStore'
import { AutomaticResearchFindings } from './AutomaticResearchFindings'
import { PortfolioStudyAnalysis } from './PortfolioAnalysis'
import { PortfolioContinuousReport } from './PortfolioContinuousReport'
import { BackToStudy, PortfolioStudyWorkspace } from './PortfolioStudyWorkspace'
import { PortfolioTrials } from './PortfolioTrials'
import { reportMoney, useReportCurrency } from './ReportCurrency'
import { ResearchResultReview } from './ResearchResultReview'
import { researchResultPeriod, researchResultRole } from './researchPresentation'
import { SaveToShortlist } from './SaveToShortlist'
import { usePortfolioAnalysis } from './usePortfolioAnalysis'
import { useReportPreferences } from './useReportPreferences'
import { readStudyView, studyWorkspaceIdentity, writeStudyView } from './useStudyWorkspaceView'

const number = (value: unknown, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`
    : '—'
export const portfolioMoney = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('en-IN', { style: 'currency', currency: 'INR' })
    : '—'
const stamp = (value: unknown) =>
  typeof value === 'string' ? value.replace('T', ' ').replace('+05:30', ' IST') : '—'
const td = 'whitespace-nowrap px-3 py-3 text-left text-sm'
const th = `${td} text-xs font-medium text-muted-foreground`
function Pager({
  page,
  count,
  onChange,
}: {
  page: number
  count: number
  onChange: (page: number) => void
}) {
  if (count <= 25) return null
  return (
    <div className="flex items-center justify-end gap-3 pt-3 text-xs text-muted-foreground">
      <span>
        {page * 25 + 1}–{Math.min(count, (page + 1) * 25)} of {count}
      </span>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={page === 0}
        onClick={() => onChange(page - 1)}
      >
        Previous
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={(page + 1) * 25 >= count}
        onClick={() => onChange(page + 1)}
      >
        Next
      </Button>
    </div>
  )
}
export function StrategySettings({ strategy }: { strategy: PortfolioSettings }) {
  const config = strategy.config
  const settings = [
    ['Allocation', number(strategy.allocation_pct, '%')],
    ['Per-trade size', number(config.order_size_pct, '% of allocation')],
    ['Profit target', number(config.target_pct, '%')],
    ['Stop loss', number(config.stop_pct, '%')],
    [
      'Holding limit',
      config.hold_minutes != null
        ? number(config.hold_minutes, ' minutes')
        : number(config.hold_sessions, ' trading sessions'),
    ],
    ['Close within the day', config.trade_horizon === 'intraday' ? 'Yes' : 'No'],
    ['Trailing stop', config.trailing_enabled ? number(config.trailing_pct, '%') : 'Off'],
    ['Costs per side', number(config.cost_bps, ' bps')],
    ['Slippage per side', number(config.slippage_bps, ' bps')],
    ...(config.entry_time ? [['Enter after', `${config.entry_time} IST`]] : []),
    ...(config.exit_time ? [['Exit by', `${config.exit_time} IST`]] : []),
  ]
  return (
    <div className="space-y-3 py-5">
      <h3 className="break-words font-medium">{strategy.name}</h3>
      <dl className="grid gap-x-12 gap-y-2 text-sm sm:grid-cols-2">
        {settings.map(([label, value]) => (
          <div className="flex min-w-0 justify-between gap-4" key={label}>
            <dt className="min-w-0 break-words text-muted-foreground">{label}</dt>
            <dd className="max-w-[60%] break-words text-right tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
interface ResultProps {
  job: PortfolioJob
  result: PortfolioResult
  onRerun: (trialId?: string) => void
  rerunning: boolean
  exportUrl: string
  onAdjust?: (trialId?: string) => void
  onOptimize?: () => void
  onEvaluate?: () => void
  embedded?: boolean
  readOnly?: boolean
  onOpenReport?: (jobId: string) => void
  studyReport?: boolean
  onStudyReportChange?: (open: boolean) => void
  experimentId?: string
  freezeAnalysis?: boolean
  hideStudyBack?: boolean
}
export function PortfolioResults(props: ResultProps) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const identity = studyWorkspaceIdentity(
    owner,
    props.job.id,
    props.result.report_context?.result_artifact ?? props.job.id,
    props.experimentId
  )
  const [period, setPeriod] = useState<'earlier' | 'later'>('earlier')
  const [localReport, updateShowReport] = useState(
    () => readStudyView(identity).surface === 'report'
  )
  const showReport = props.studyReport ?? localReport
  function setShowReport(open: boolean) {
    writeStudyView(identity, { ...readStudyView(identity), surface: open ? 'report' : 'study' })
    updateShowReport(open)
    props.onStudyReportChange?.(open)
  }
  const connectedStudy = Boolean(props.result.experiment && props.onOpenReport)
  const validation = props.result.validation
  if (props.freezeAnalysis)
    return (
      <PortfolioReport
        {...props}
        result={{
          ...props.result,
          experiment: undefined,
          validation: undefined,
          reserved_evaluation: undefined,
        }}
        readOnly
        hideStudyTabs
        freezeAnalysis
        onAdjust={undefined}
        onOptimize={undefined}
        onEvaluate={undefined}
        experimentId={undefined}
      />
    )
  if (connectedStudy && !showReport)
    return (
      <PortfolioStudyWorkspace
        {...props}
        readOnly={props.readOnly ?? false}
        renderSettings={(strategy) => <StrategySettings strategy={strategy} />}
        onOpenReport={(jobId) => {
          if (jobId === props.job.id) setShowReport(true)
          else props.onOpenReport?.(jobId)
        }}
      />
    )
  const back =
    connectedStudy && !props.hideStudyBack ? (
      <BackToStudy onClick={() => setShowReport(false)} />
    ) : null
  if (!validation)
    return (
      <div>
        {back}
        <PortfolioReport {...props} hideStudyTabs={connectedStudy} />
      </div>
    )
  const result =
    period === 'later'
      ? {
          ...validation.result,
          portfolio: props.result.portfolio,
          source: validation.result.source ?? props.result.source,
        }
      : props.result
  return (
    <div className="space-y-5">
      {back}
      <fieldset className="inline-flex rounded-lg bg-muted p-1" aria-label="Evaluation period">
        {(['earlier', 'later'] as const).map((value) => (
          <Button
            type="button"
            key={value}
            size="sm"
            variant={period === value ? 'secondary' : 'ghost'}
            aria-pressed={period === value}
            onClick={() => setPeriod(value)}
          >
            {props.result.automatic_research
              ? value === 'earlier'
                ? 'Search period'
                : 'Final check'
              : value === 'earlier'
                ? 'Earlier period'
                : 'Later period'}
          </Button>
        ))}
      </fieldset>
      <PortfolioReport
        key={period}
        {...props}
        result={result}
        laterPeriod={period === 'later'}
        hideStudyTabs={connectedStudy}
      />
    </div>
  )
}
function PortfolioReport({
  job,
  result: initialResult,
  onRerun,
  rerunning,
  exportUrl,
  onAdjust,
  onOptimize,
  onEvaluate,
  embedded = false,
  readOnly = false,
  laterPeriod = false,
  hideStudyTabs = false,
  experimentId,
  freezeAnalysis = false,
}: ResultProps & { laterPeriod?: boolean; hideStudyTabs?: boolean }) {
  const owner = useAuthStore((state) => state.user?.username)
  const preferences = useReportPreferences(owner)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsOpener = useRef<HTMLElement | null>(null)
  const [tab, setTab] = useState('report')
  const [strategyFilter, setStrategyFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [exactSymbol, setExactSymbol] = useState<string | null>(null)
  const [tradePage, setTradePage] = useState(0)
  const [trialPage, setTrialPage] = useState(0)
  const analysis = usePortfolioAnalysis(
    job.id,
    initialResult,
    tab === 'report' || tab === 'study',
    laterPeriod,
    freezeAnalysis
  )
  const result = analysis.result
  const automatic = Boolean(result.automatic_research || job.result?.automatic_research)
  const currency = useReportCurrency()
  const money = (value: unknown) => reportMoney(value, currency)
  const analysisActions = {
    busy: analysis.busy,
    response: analysis.response,
    onPrepare: analysis.prepare,
    readOnly,
    freezeAnalysis,
    exportUrl: portfolioResearch.analysisExportUrl(job.id),
  }
  const summary = result.summary
  const experiment = result.experiment
  const trades = useMemo(
    () =>
      result.ledger.filter(
        (trade) =>
          (strategyFilter === 'all' || trade.strategy_id === strategyFilter) &&
          (statusFilter === 'all' || trade.status === statusFilter) &&
          (exactSymbol
            ? trade.symbol === exactSymbol
            : !query || String(trade.symbol).toLowerCase().includes(query.toLowerCase()))
      ),
    [result.ledger, strategyFilter, statusFilter, query, exactSymbol]
  )
  const pending = Number(summary.pending_trades ?? 0) + Number(summary.unfunded_pending ?? 0)
  const otherExclusions = Math.max(
    0,
    Number(summary.excluded_signals ?? 0) - Number(result.source?.excluded_signals ?? 0)
  )
  const settingsContent = (
    <>
      <div className="space-y-1 border-b pb-4 text-sm">
        <p>Shared starting cash: {money(summary.initial_capital)}</p>
        <p className="text-muted-foreground">
          {(result.execution?.engine ?? result.portfolio?.engine) === 'nautilus'
            ? 'NautilusTrader'
            : 'VectorBT'}
          {result.execution?.engine_version ? ` ${result.execution.engine_version}` : ''}
          {experiment ? ` · Optuna ${experiment.optimizer.version}` : ''}
        </p>
      </div>
      <div className="divide-y">
        {result.strategies.map((strategy) => (
          <StrategySettings key={strategy.id} strategy={strategy} />
        ))}
      </div>
    </>
  )
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2
            className="text-xl font-semibold focus:outline-none"
            data-research-navigation-heading
            tabIndex={-1}
          >
            {embedded
              ? researchResultRole({ ...job, result }, true)
              : (result.portfolio?.name ??
                job.specification?.portfolio?.name ??
                'Portfolio result')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {(result.execution?.engine ?? result.portfolio?.engine) === 'nautilus'
              ? 'NautilusTrader'
              : 'VectorBT'}{' '}
            ·{' '}
            {(result.source?.interval ?? result.execution?.interval) === '1m'
              ? 'Minute candles'
              : 'Daily candles'}
            {researchResultPeriod({ ...job, result })
              ? ` · ${researchResultPeriod({ ...job, result })}`
              : ''}
            {!embedded ? ` · ${researchResultRole({ ...job, result }, true)}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {automatic && !laterPeriod && !readOnly && (
            <Button disabled={rerunning} onClick={() => onRerun()}>
              {rerunning ? 'Starting…' : 'Backtest these settings'}
            </Button>
          )}
          {!laterPeriod &&
            !automatic &&
            result.report_context?.period !== 'evaluation' &&
            onOptimize &&
            !readOnly && (
              <Button type="button" disabled={rerunning} onClick={onOptimize}>
                Optimize this
              </Button>
            )}
          {!laterPeriod &&
            !automatic &&
            result.report_context?.period !== 'evaluation' &&
            onAdjust &&
            !readOnly && (
              <Button
                type="button"
                variant="outline"
                disabled={rerunning}
                onClick={() => onAdjust()}
              >
                Adjust & test
              </Button>
            )}
          {!automatic &&
            !laterPeriod &&
            result.report_context?.period !== 'evaluation' &&
            experimentId && (
              <SaveToShortlist
                experimentId={experimentId}
                jobId={job.id}
                configId={
                  result.experiment
                    ? (result.report_context?.config_id ?? result.experiment.recommendation_id)
                    : undefined
                }
                proposalNumber={
                  result.experiment ? result.report_context?.candidate?.trial_number : undefined
                }
                readOnly={readOnly}
              />
            )}
          {!freezeAnalysis && (
            <Button variant="ghost" asChild>
              <a href={exportUrl} download>
                Export
              </a>
            </Button>
          )}
          {!automatic && !laterPeriod && !readOnly && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" size="icon" variant="ghost" aria-label="More report actions">
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem disabled={rerunning} onSelect={() => onRerun()}>
                  {rerunning ? 'Starting…' : onAdjust ? 'Replay exact' : 'Run again'}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>
      {result.automatic_research && (
        <AutomaticResearchFindings findings={result.automatic_research} />
      )}
      {automatic && !laterPeriod && !readOnly && (
        <p className="text-xs text-muted-foreground">
          Open an exact backtest to compare or save this setup.
        </p>
      )}
      {!automatic && experimentId && result.report_context && !freezeAnalysis && (
        <ResearchResultReview
          experimentId={experimentId}
          job={job}
          result={result}
          readOnly={readOnly}
        />
      )}
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer">Report details</summary>
        <dl className="mt-3 grid gap-2 sm:grid-cols-2">
          <div>
            <dt>Price source</dt>
            <dd>{result.source?.provider ?? 'OpenAlgo prices'}</dd>
          </div>
          <div>
            <dt>Engine</dt>
            <dd>
              {(result.execution?.engine ?? result.portfolio?.engine) === 'nautilus'
                ? 'NautilusTrader'
                : 'VectorBT'}{' '}
              {result.execution?.engine_version ?? ''}
            </dd>
          </div>
          {result.report_context?.candidate && (
            <div>
              <dt>Study candidate</dt>
              <dd>
                Trial {result.report_context.candidate.trial_number + 1}
                {result.report_context.candidate.is_objective_winner ? ' · Best by objective' : ''}
              </dd>
            </div>
          )}
          {result.analysis && (
            <div>
              <dt>Statistics version</dt>
              <dd>{result.analysis.version}</dd>
            </div>
          )}
        </dl>
      </details>
      {!experimentId && result.reserved_evaluation && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-muted-foreground">
            Later period reserved · {result.reserved_evaluation.evaluation.from} –{' '}
            {result.reserved_evaluation.evaluation.to}
          </span>
          {onEvaluate && !readOnly && (
            <Button size="sm" variant="outline" disabled={rerunning} onClick={onEvaluate}>
              Test later period
            </Button>
          )}
        </div>
      )}
      {Number(result.source?.excluded_signals) > 0 && (
        <button
          type="button"
          className="text-left text-sm text-muted-foreground underline-offset-4 hover:underline"
          onClick={() => {
            setStatusFilter('excluded')
            setStrategyFilter('all')
            setQuery('')
            setExactSymbol(null)
            setTradePage(0)
            setTab('trades')
          }}
        >
          {result.source?.excluded_signals}{' '}
          {result.source?.excluded_signals === 1 ? 'signal' : 'signals'} excluded — view reasons
        </button>
      )}
      {experiment && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <span className="font-medium">
            {result.automatic_research ? 'Search period report' : 'Best by objective'} ·{' '}
            {new Set(experiment.rows.map((row) => row.config_id)).size} tested portfolios
          </span>
          <span className="text-muted-foreground">
            {experiment.specification.objective === 'return'
              ? 'Highest return'
              : experiment.specification.objective === 'drawdown'
                ? 'Lowest drawdown'
                : 'Balanced return & drawdown'}
          </span>
        </div>
      )}
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="report">Report</TabsTrigger>
          <TabsTrigger value="trades">Trades</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
          {experiment && !hideStudyTabs && <TabsTrigger value="trials">Trials</TabsTrigger>}
          {experiment && !hideStudyTabs && <TabsTrigger value="study">Study analysis</TabsTrigger>}
        </TabsList>
        <TabsContent value="report" className="pt-3">
          <PortfolioContinuousReport
            key={owner}
            result={result}
            preferences={preferences}
            {...analysisActions}
            onSettings={() => {
              settingsOpener.current =
                document.activeElement instanceof HTMLElement ? document.activeElement : null
              setSettingsOpen(true)
            }}
            onTrades={(strategyId, symbol) => {
              setStrategyFilter(strategyId ?? 'all')
              setStatusFilter('all')
              setQuery(symbol ?? '')
              setExactSymbol(symbol ?? null)
              setTradePage(0)
              setTab('trades')
            }}
          />
          {(pending > 0 || Number(summary.skipped_trades) > 0 || otherExclusions > 0) && (
            <Button
              type="button"
              variant="link"
              className="h-auto px-0 pt-4 text-muted-foreground"
              onClick={() => setTab('trades')}
            >
              {[
                pending > 0 ? `${pending} pending` : '',
                Number(summary.skipped_trades) > 0 ? `${summary.skipped_trades} skipped` : '',
                otherExclusions > 0 ? `${otherExclusions} excluded` : '',
              ]
                .filter(Boolean)
                .join(' · ')}{' '}
              — view trades
            </Button>
          )}
        </TabsContent>
        {experiment && (
          <TabsContent value="study" className="pt-5">
            <PortfolioStudyAnalysis result={result} {...analysisActions} />
          </TabsContent>
        )}
        <TabsContent value="trades" className="space-y-4 pt-4">
          <div className="flex flex-wrap gap-3">
            <Input
              className="sm:w-44"
              aria-label="Find symbol"
              placeholder="Find symbol"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value)
                setExactSymbol(null)
                setTradePage(0)
              }}
            />
            <select
              className="h-10 rounded-md border bg-background px-3 text-sm"
              aria-label="Filter trades by strategy"
              value={strategyFilter}
              onChange={(event) => {
                setStrategyFilter(event.target.value)
                setTradePage(0)
              }}
            >
              <option value="all">All strategies</option>
              {result.strategies.map((strategy) => (
                <option key={strategy.id} value={strategy.id}>
                  {strategy.name}
                </option>
              ))}
            </select>
            <select
              className="h-10 rounded-md border bg-background px-3 text-sm"
              aria-label="Filter trades by status"
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value)
                setTradePage(0)
              }}
            >
              <option value="all">All outcomes</option>
              <option value="closed">Closed</option>
              <option value="pending">Pending</option>
              <option value="skipped">Skipped</option>
              <option value="excluded">Excluded</option>
            </select>
          </div>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full">
              <thead className="bg-muted/40">
                <tr>
                  {['Strategy / symbol', 'Entry', 'Exit', 'Quantity', 'Net P&L', 'Outcome'].map(
                    (label) => (
                      <th key={label} className={th}>
                        {label}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody className="divide-y">
                {trades.slice(tradePage * 25, (tradePage + 1) * 25).map((trade, index) => (
                  <tr key={`${trade.strategy_id}-${trade.source_row}-${trade.symbol}-${index}`}>
                    <td className={td}>
                      <span className="font-medium">{trade.symbol}</span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {trade.strategy_name}
                      </span>
                    </td>
                    <td className={td}>
                      {stamp(trade.entry_timestamp ?? trade.entry_date)}
                      <span className="block text-xs text-muted-foreground">
                        {money(trade.entry_price)}
                      </span>
                    </td>
                    <td className={td}>
                      {stamp(trade.exit_timestamp ?? trade.exit_date)}
                      <span className="block text-xs text-muted-foreground">
                        {money(trade.exit_price)}
                      </span>
                    </td>
                    <td className={td}>{number(trade.quantity)}</td>
                    <td className={td}>{money(trade.pnl)}</td>
                    <td className={td}>
                      <details>
                        <summary className="cursor-pointer capitalize">{trade.status}</summary>
                        <p className="mt-2 max-w-60 whitespace-normal text-xs text-muted-foreground">
                          {String(trade.reason ?? '')}
                        </p>
                      </details>
                    </td>
                  </tr>
                ))}
                {!trades.length && (
                  <tr>
                    <td colSpan={6} className="p-8 text-center text-sm text-muted-foreground">
                      No trades match these filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager page={tradePage} count={trades.length} onChange={setTradePage} />
        </TabsContent>
        <TabsContent value="settings" className="pt-4">
          {settingsContent}
        </TabsContent>
        {experiment && (
          <TabsContent value="trials" className="space-y-4 pt-4">
            <PortfolioTrials
              key={job.id}
              experiment={experiment}
              page={trialPage}
              onPageChange={setTrialPage}
              onRerun={onRerun}
              rerunning={rerunning}
              readOnly={readOnly}
              renderSettings={(strategy) => <StrategySettings strategy={strategy} />}
            />
          </TabsContent>
        )}
      </Tabs>
      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent
          className="max-h-[85dvh] overflow-y-auto sm:max-w-2xl motion-reduce:animate-none"
          onCloseAutoFocus={(event) => {
            if (settingsOpener.current?.isConnected) {
              event.preventDefault()
              settingsOpener.current.focus()
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>Report settings</DialogTitle>
            <DialogDescription>The exact settings used for this result.</DialogDescription>
          </DialogHeader>
          {settingsContent}
        </DialogContent>
      </Dialog>
    </div>
  )
}
