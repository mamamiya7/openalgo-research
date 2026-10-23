import { useQuery } from '@tanstack/react-query'
import { MoreHorizontal } from 'lucide-react'
import { useMemo, useState } from 'react'
import {
  type PortfolioJob,
  type PortfolioResult,
  type PortfolioSettings,
  portfolioResearch,
} from '@/api/portfolioResearch'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuthStore } from '@/stores/authStore'
import {
  AutomaticResearchFindings,
  CapitalPlanning,
  ResearchHistoryScope,
  recordedHistoryAvailability,
} from './AutomaticResearchFindings'
import { entryConditionRegimeName } from './ChosenEntryConditions'
import { PortfolioStudyAnalysis } from './PortfolioAnalysis'
import { PortfolioContinuousReport } from './PortfolioContinuousReport'
import { BackToStudy, PortfolioStudyWorkspace } from './PortfolioStudyWorkspace'
import { PortfolioTrials } from './PortfolioTrials'
import { PrepareTestedReport } from './PrepareTestedReport'
import { reportMoney, useReportCurrency } from './ReportCurrency'
import { ResearchResultReview } from './ResearchResultReview'
import {
  entryPriorityLabel,
  researchResultPeriod,
  researchResultRole,
} from './researchPresentation'
import { SavedTradeChartDialog } from './SavedTradeChartDialog'
import { SaveTestedSetup } from './SaveTestedSetup'
import { SaveToShortlist } from './SaveToShortlist'
import { useConditionReplay } from './useConditionReplay'
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
  const policy = strategy.execution_policy
  const condition = strategy.entry_condition
  const settings = [
    ...(condition
      ? [
          [
            'Entry condition',
            'hypothesis' in condition
              ? condition.hypothesis.label
              : entryConditionRegimeName(condition.regime),
          ],
        ]
      : []),
    ['Allocation', number(strategy.allocation_pct, '%')],
    ...(policy?.allocation_mode
      ? [
          [
            'Portfolio allocation',
            policy.allocation_mode === 'prior60_cluster_inverse_vol'
              ? 'Cluster inverse volatility · new entries'
              : 'Original allocation',
          ],
        ]
      : []),
    ['Per-trade size', number(config.order_size_pct, '% of allocation')],
    ...(policy?.entry_priority
      ? [['Simultaneous entries', entryPriorityLabel(policy.entry_priority)]]
      : []),
    [
      'Profit target',
      policy?.exit_mode === 'atr'
        ? `${policy.atr_target_multiple}× ATR(${policy.atr_period})`
        : number(config.target_pct, '%'),
    ],
    [
      'Stop loss',
      policy?.exit_mode === 'atr'
        ? `${policy.atr_stop_multiple}× ATR(${policy.atr_period})`
        : number(config.stop_pct, '%'),
    ],
    ...(policy
      ? [
          [
            'Sizing',
            policy.sizing === 'stop_risk' ? 'Stop risk within trade budget' : 'Allocation',
          ],
          ...(policy.sizing === 'stop_risk'
            ? [
                [
                  'Target risk per trade',
                  number(
                    policy.risk_limits.max_trade_risk_pct * (policy.risk_budget_fraction ?? 0.5),
                    '%'
                  ),
                ],
              ]
            : []),
          ['Gross exposure limit', number(policy.risk_limits.max_gross_exposure_pct, '%')],
          ['Per-symbol limit', number(policy.risk_limits.max_symbol_exposure_pct, '%')],
          ['Open position limit', number(policy.risk_limits.max_open_positions)],
          ['Risk per trade', number(policy.risk_limits.max_trade_risk_pct, '%')],
          ['Total stop risk limit', number(policy.risk_limits.max_total_stop_risk_pct, '%')],
          ...(['research-risk-limits-v2', 'research-risk-limits-v3'].includes(
            policy.risk_limits.version
          )
            ? [['Daily buy budget', number(policy.risk_limits.max_daily_buy_turnover_pct, '%')]]
            : []),
          ...(policy.capacity_mode === 'prior20_broker_reported'
            ? [
                [
                  'Daily volume participation',
                  number(policy.risk_limits.max_daily_volume_participation_pct, '%'),
                ],
              ]
            : []),
          ...(policy.correlation_mode === 'prior60_positive_080'
            ? [
                [
                  'Correlated exposure limit',
                  number(policy.risk_limits.max_correlated_exposure_pct, '%'),
                ],
              ]
            : []),
        ]
      : []),
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
function ParentResearchHistoryScope({
  parentId,
  owner,
  selectionConfigId,
}: {
  parentId: string
  owner: string
  selectionConfigId?: string | null
}) {
  const parent = useQuery({
    queryKey: ['portfolio-job', owner, parentId],
    queryFn: ({ signal }) => portfolioResearch.job(parentId, signal),
    staleTime: Infinity,
    retry: false,
  })
  const findings = parent.data?.result?.automatic_research
  const availability = recordedHistoryAvailability(findings)
  return (
    <>
      {availability && <ResearchHistoryScope availability={availability} />}
      {selectionConfigId === findings?.selected_config_id && findings?.capital_planning && (
        <CapitalPlanning planning={findings.capital_planning} />
      )}
    </>
  )
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
    writeStudyView(identity, {
      ...readStudyView(identity),
      surface: open ? 'report' : 'study',
    })
    updateShowReport(open)
    props.onStudyReportChange?.(open)
  }
  const connectedStudy = Boolean(props.result.experiment && props.onOpenReport)
  const validation = props.result.validation
  const parentAutomatic = props.result.automatic_research ?? props.job.result?.automatic_research
  const historyAvailability = recordedHistoryAvailability(parentAutomatic)
  const parentStudyId =
    props.result.report_context?.candidate?.study_job_id ??
    props.result.report_context?.parent_job_id
  const selectionConfigId =
    props.result.report_context?.period === 'selection'
      ? props.result.report_context.config_id
      : undefined
  const inheritedHistoryScope =
    historyAvailability && !props.result.automatic_research ? (
      <>
        <ResearchHistoryScope availability={historyAvailability} />
        {selectionConfigId === parentAutomatic?.selected_config_id &&
          parentAutomatic?.capital_planning && (
            <CapitalPlanning planning={parentAutomatic.capital_planning} />
          )}
      </>
    ) : !parentAutomatic && parentStudyId && parentStudyId !== props.job.id ? (
      <ParentResearchHistoryScope
        parentId={parentStudyId}
        owner={owner}
        selectionConfigId={selectionConfigId}
      />
    ) : null
  const historical =
    parentAutomatic?.version === 'automatic-hypotheses-v16' &&
    (parentAutomatic.status === 'historical' ||
      parentAutomatic.recipe.validation_mode === 'historical_only' ||
      Boolean(historyAvailability))
  if (props.freezeAnalysis)
    return (
      <>
        {inheritedHistoryScope}
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
      </>
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
        {inheritedHistoryScope}
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
      {historical && period === 'later' && (
        <p className="text-sm font-medium">
          {historyAvailability
            ? 'Historical research · not independently validated'
            : 'Historical optimisation — not independently validated'}
        </p>
      )}
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
                : historical
                  ? 'Historical comparison'
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
  onOpenReport,
}: ResultProps & { laterPeriod?: boolean; hideStudyTabs?: boolean }) {
  const owner = useAuthStore((state) => state.user?.username)
  const preferences = useReportPreferences(owner)
  const [tab, setTab] = useState('report')
  const [strategyFilter, setStrategyFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [exactSymbol, setExactSymbol] = useState<string | null>(null)
  const [tradePage, setTradePage] = useState(0)
  const [trialPage, setTrialPage] = useState(0)
  const [chartSelection, setChartSelection] = useState<{
    index: number
    reportId: string
    owner: string | undefined
    opener: HTMLElement
  } | null>(null)
  const analysis = usePortfolioAnalysis(
    job.id,
    initialResult,
    tab === 'report' || tab === 'study',
    laterPeriod,
    freezeAnalysis
  )
  const result = analysis.result
  const conditionResult = Boolean(result.condition_replay)
  const conditionRestricted = conditionResult && result.condition_replay?.period !== 'selection'
  const canTestCondition =
    !readOnly &&
    !freezeAnalysis &&
    !conditionResult &&
    Boolean(experimentId) &&
    Boolean(onOpenReport) &&
    Boolean(result.report_context?.analysis_artifact) &&
    (result.execution?.engine ?? result.portfolio?.engine ?? 'vectorbt') === 'vectorbt'
  const conditionReplay = useConditionReplay(
    job.id,
    experimentId,
    result.report_context?.analysis_artifact,
    laterPeriod ? 'validation' : 'selection',
    onOpenReport,
    !canTestCondition || analysis.busy
  )
  const automatic = Boolean(result.automatic_research || job.result?.automatic_research)
  const currency = useReportCurrency()
  const money = (value: unknown) => reportMoney(value, currency)
  const analysisActions = {
    conditionReplay: canTestCondition ? conditionReplay : undefined,
    busy: analysis.busy,
    response: analysis.response,
    onPrepare: analysis.prepare,
    onCancel: analysis.cancel,
    readOnly,
    freezeAnalysis,
    exportUrl: portfolioResearch.analysisExportUrl(job.id),
  }
  const summary = result.summary
  const experiment = result.experiment
  const trades = useMemo(
    () =>
      result.ledger
        .map((trade, ledgerIndex) => ({ trade, ledgerIndex }))
        .filter(
          ({ trade }) =>
            (strategyFilter === 'all' || trade.strategy_id === strategyFilter) &&
            (statusFilter === 'all' || trade.status === statusFilter) &&
            (exactSymbol
              ? trade.symbol === exactSymbol
              : !query || String(trade.symbol).toLowerCase().includes(query.toLowerCase()))
        ),
    [result.ledger, strategyFilter, statusFilter, query, exactSymbol]
  )
  const pending = Number(summary.pending_trades ?? 0) + Number(summary.unfunded_pending ?? 0)
  const chartContext = result.report_context
  const selectedTrade =
    chartSelection &&
    chartSelection.owner === owner &&
    chartSelection.reportId === chartContext?.report_id
      ? result.ledger[chartSelection.index]
      : undefined
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
          {!conditionRestricted &&
            !laterPeriod &&
            !automatic &&
            !freezeAnalysis &&
            experimentId &&
            result.report_context && (
              <SaveTestedSetup
                key={JSON.stringify([
                  job.id,
                  result.report_context.config_id,
                  result.report_context.analysis_artifact,
                ])}
                experimentId={experimentId}
                name={
                  result.report_context.candidate?.trial_number != null
                    ? `Trial ${result.report_context.candidate.trial_number + 1}`
                    : (result.portfolio?.name ?? 'Tested setup')
                }
                target={{
                  job_id: job.id,
                  config_id: result.report_context.config_id ?? undefined,
                  expected_report: {
                    result_artifact: result.report_context.result_artifact,
                    analysis_artifact: result.report_context.analysis_artifact,
                  },
                }}
                readOnly={readOnly}
              />
            )}
          {automatic && !laterPeriod && experiment && onOpenReport && (
            <PrepareTestedReport
              jobId={job.id}
              configId={experiment.recommendation_id}
              readOnly={readOnly}
              onOpen={onOpenReport}
            />
          )}
          {automatic && !laterPeriod && !readOnly && (!experiment || !onOpenReport) && (
            <Button disabled={rerunning} onClick={() => onRerun()}>
              {rerunning ? 'Starting…' : 'Backtest these settings'}
            </Button>
          )}
          {!conditionRestricted &&
            !laterPeriod &&
            !automatic &&
            result.report_context?.period !== 'evaluation' &&
            onOptimize &&
            !readOnly && (
              <Button type="button" variant="outline" disabled={rerunning} onClick={onOptimize}>
                Optimize this
              </Button>
            )}
          {!conditionRestricted &&
            !laterPeriod &&
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
          {!conditionRestricted &&
            !automatic &&
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
                  result.experiment
                    ? (result.report_context?.candidate?.trial_number ?? undefined)
                    : undefined
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
                  {rerunning
                    ? 'Starting…'
                    : onAdjust || conditionResult
                      ? 'Replay exact'
                      : 'Run again'}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>
      {result.automatic_research && (
        <AutomaticResearchFindings
          findings={result.automatic_research}
          selectedSettings={result.experiment?.selected_strategies}
          developmentEvidence={result.development_evidence}
        />
      )}
      {!conditionRestricted &&
        !automatic &&
        experimentId &&
        result.report_context &&
        !freezeAnalysis && (
          <ResearchResultReview
            experimentId={experimentId}
            job={job}
            result={result}
            readOnly={readOnly}
            showSave={false}
          />
        )}
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
                {result.report_context.candidate.trial_number == null
                  ? 'Selected original settings'
                  : `Trial ${result.report_context.candidate.trial_number + 1}`}
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
      {!conditionResult && !experimentId && result.reserved_evaluation && (
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
            onTrades={(strategyId, symbol) => {
              setStrategyFilter(strategyId ?? 'all')
              setStatusFilter('all')
              setQuery(symbol ?? '')
              setExactSymbol(symbol ?? null)
              setTradePage(0)
              setTab('trades')
            }}
          />
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
                  {chartContext && (
                    <th className={th}>
                      <span className="sr-only">Chart</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y">
                {trades
                  .slice(tradePage * 25, (tradePage + 1) * 25)
                  .map(({ trade, ledgerIndex }) => (
                    <tr
                      key={`${trade.strategy_id}-${trade.source_row}-${trade.symbol}-${ledgerIndex}`}
                    >
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
                      {chartContext && (
                        <td className={td}>
                          {['closed', 'pending', 'open'].includes(String(trade.status)) &&
                            typeof trade.entry_price === 'number' &&
                            Number.isFinite(trade.entry_price) &&
                            trade.entry_price > 0 &&
                            typeof trade.quantity === 'number' &&
                            trade.quantity > 0 && (
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                aria-label={`View ${trade.symbol} trade on chart`}
                                onClick={(event) =>
                                  setChartSelection({
                                    index: ledgerIndex,
                                    reportId: chartContext.report_id,
                                    owner,
                                    opener: event.currentTarget,
                                  })
                                }
                              >
                                View on chart
                              </Button>
                            )}
                        </td>
                      )}
                    </tr>
                  ))}
                {!trades.length && (
                  <tr>
                    <td
                      colSpan={chartContext ? 7 : 6}
                      className="p-8 text-center text-sm text-muted-foreground"
                    >
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
      {selectedTrade && chartSelection && chartContext && (
        <SavedTradeChartDialog
          key={`${owner}|${chartContext.report_id}|${chartSelection.index}`}
          request={{
            jobId: chartContext.job_id,
            resultArtifact: chartContext.result_artifact,
            period: chartContext.period,
            tradeIndex: chartSelection.index,
          }}
          symbol={String(selectedTrade.symbol)}
          strategy={String(selectedTrade.strategy_name ?? '')}
          opener={chartSelection.opener}
          onClose={() => setChartSelection(null)}
        />
      )}
    </div>
  )
}
