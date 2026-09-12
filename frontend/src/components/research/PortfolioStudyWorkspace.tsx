import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import {
  type AnalysisChart,
  type PortfolioJob,
  type PortfolioResult,
  type PortfolioSettings,
  portfolioResearch,
} from '@/api/portfolioResearch'
import { type CandidateReportReceipt, researchCandidates } from '@/api/researchCandidates'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuthStore } from '@/stores/authStore'
import { AnalysisFigure } from './AnalysisCharts'
import { AnalysisMetricTable } from './AnalysisMetricTable'
import { AnalysisBasis, AnalysisPreparation } from './PortfolioAnalysis'
import { PortfolioTrials } from './PortfolioTrials'
import { availableTrialMetrics, trialMetricText, trialMetricValue } from './portfolioTrialMetrics'
import { SaveToShortlist } from './SaveToShortlist'
import { StudyActivity } from './StudyActivity'
import {
  connectedStudyChart,
  pointsFromChart,
  returnDrawdownChart,
  type StudyPoint,
  studyCounts,
  studyParameterLabels,
  studyPoint,
} from './studyPresentation'
import { usePortfolioAnalysis } from './usePortfolioAnalysis'
import { studyWorkspaceIdentity, useStudyWorkspaceView } from './useStudyWorkspaceView'

const number = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
    : '—'
interface Props {
  job: PortfolioJob
  result: PortfolioResult
  readOnly: boolean
  rerunning: boolean
  onRerun: (configId: string) => void
  onAdjust?: (configId: string) => void
  onOpenReport: (jobId: string) => void
  renderSettings: (strategy: PortfolioSettings) => ReactNode
  experimentId?: string
}

export function PortfolioStudyWorkspace(props: Props) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const identity = studyWorkspaceIdentity(
    owner,
    props.job.id,
    props.result.report_context?.result_artifact ?? props.job.id,
    props.experimentId
  )
  return <StudyWorkspace key={identity} {...props} identity={identity} owner={owner} />
}

function StudyWorkspace({
  job,
  result: original,
  readOnly,
  rerunning,
  onRerun,
  onAdjust,
  onOpenReport,
  renderSettings,
  identity,
  owner,
  experimentId,
}: Props & { identity: string; owner: string }) {
  const [view, setView] = useStudyWorkspaceView(identity)
  const [overlapping, setOverlapping] = useState<StudyPoint[]>([])
  const [preparing, setPreparing] = useState(false)
  const [prepareError, setPrepareError] = useState<string | null>(null)
  const [candidateTab, setCandidateTab] = useState('settings')
  const request = useRef<AbortController | null>(null)
  const opener = useRef<HTMLElement | null>(null)
  const analysis = usePortfolioAnalysis(job.id, original, true, false)
  const result = analysis.result
  const experiment = result.experiment!
  const native = experiment.study_analysis
  const queryClient = useQueryClient()
  const reportsKey = ['research-candidates', owner, job.id]
  const reports = useQuery({
    queryKey: reportsKey,
    queryFn: ({ signal }) => researchCandidates.get(job.id, signal),
    retry: false,
    staleTime: 15000,
    refetchInterval: (query) =>
      query.state.data?.candidates.some((item) => ['queued', 'running'].includes(item.status))
        ? 1500
        : false,
  })
  useEffect(
    () => () => {
      request.current?.abort()
    },
    []
  )
  const counts = studyCounts(experiment)
  const winner = experiment.rows.find((row) => row.config_id === experiment.recommendation_id)
  const candidate =
    view.candidate && studyPoint(experiment, view.candidate.configId, view.candidate.proposalNumber)
  const row = candidate && experiment.rows.find((item) => item.config_id === candidate.configId)
  const proposal =
    candidate && experiment.trials?.find((item) => item.number === candidate.proposalNumber)
  const receipt = (configId: string): CandidateReportReceipt | undefined =>
    reports.data?.candidates.find((item) => item.config_id === configId) ??
    (configId === experiment.recommendation_id
      ? {
          config_id: configId,
          trial_number: winner?.trial_number ?? 0,
          is_objective_winner: true,
          status: 'ready',
          report_job_id: job.id,
        }
      : undefined)
  const report = candidate ? receipt(candidate.configId) : undefined
  const labels = useMemo(() => studyParameterLabels(result), [result])
  const axes = Object.keys(labels)
  const selected = view.parameters.length
    ? view.parameters.filter((key) => axes.includes(key))
    : native?.parameters?.length
      ? native.parameters
      : axes.slice(0, 2)
  const charts = useMemo(
    () => native?.charts.map((chart) => connectedStudyChart(chart, experiment)) ?? [],
    [native, experiment]
  )
  const scatter = useMemo(() => returnDrawdownChart(experiment), [experiment])
  const actions = {
    busy: analysis.busy,
    response: analysis.response,
    onPrepare: analysis.prepare,
    readOnly,
    exportUrl: portfolioResearch.analysisExportUrl(job.id),
  }

  function inspect(configId: string, proposalNumber?: number) {
    const point = studyPoint(experiment, configId, proposalNumber)
    if (!point) return
    if (!candidate && overlapping.length === 0)
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setPrepareError(null)
    setCandidateTab('settings')
    setOverlapping([])
    setView({
      ...view,
      candidate: point,
      trials: { ...view.trials, selectedConfigId: point.configId },
    })
  }
  function pointClicked(point: { customdata?: unknown }, chartId: string) {
    const points = pointsFromChart(experiment, point.customdata)
    if (points.length === 1) inspect(points[0].configId, points[0].proposalNumber)
    else if (points.length > 1) {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      setOverlapping(points)
    }
    if (points.length && (!opener.current || opener.current === document.body))
      opener.current = document.getElementById(`study-chart-${job.id}-${chartId}`)
  }
  const reportAction = (configId: string) => {
    const item = receipt(configId)
    return {
      label:
        item?.status === 'ready'
          ? 'Open report'
          : item?.status === 'queued' || item?.status === 'running'
            ? 'Preparing report…'
            : 'View report',
      disabled: false,
    }
  }
  function showReport(configId: string) {
    const item = receipt(configId)
    if (item?.status === 'ready' && item.report_job_id) onOpenReport(item.report_job_id)
    else inspect(configId)
  }
  async function prepare() {
    if (!candidate || preparing || readOnly || report?.status !== 'available') return
    const controller = new AbortController()
    request.current = controller
    setPreparing(true)
    setPrepareError(null)
    try {
      const next = await researchCandidates.prepare(job.id, candidate.configId, controller.signal)
      if (controller.signal.aborted) return
      queryClient.setQueryData(reportsKey, (previous: typeof reports.data) =>
        previous
          ? {
              ...previous,
              candidates: previous.candidates.map((item) =>
                item.config_id === next.config_id ? next : item
              ),
            }
          : previous
      )
      await queryClient.invalidateQueries({ queryKey: reportsKey })
    } catch (error) {
      if (!controller.signal.aborted)
        setPrepareError(
          error instanceof Error ? error.message : 'The report could not be prepared. Try again.'
        )
    } finally {
      if (!controller.signal.aborted) setPreparing(false)
      if (request.current === controller) request.current = null
    }
  }
  function figure(chart?: AnalysisChart, deferred = false) {
    if (!chart) return null
    return (
      <section
        key={chart.id}
        id={`study-chart-${job.id}-${chart.id}`}
        tabIndex={-1}
        className="min-w-0 space-y-2"
        aria-label={chart.title}
      >
        <h3 className="text-sm font-medium">{chart.title}</h3>
        {chart.status === 'available' && chart.figure ? (
          <AnalysisFigure
            chart={chart}
            height={330}
            deferred={deferred}
            parameterLabels={labels}
            onPoint={(point) => pointClicked(point, chart.id)}
          />
        ) : (
          <p className="py-8 text-sm text-muted-foreground">
            {chart.reason ?? 'This chart is not available for the saved study.'}
          </p>
        )}
      </section>
    )
  }
  function chart(id: string, deferred = false) {
    return figure(
      charts.find((item) => item.id === id),
      deferred
    )
  }

  return (
    <div className="min-w-0 space-y-7" data-testid="study-workspace">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h2
            className="text-xl font-semibold outline-none"
            tabIndex={-1}
            data-research-navigation-heading
          >
            Study results
          </h2>
          <p className="text-sm text-muted-foreground">
            {experiment.optimizer.objective_definition} · Maximize
            {result.report_context?.period_label ? ` · ${result.report_context.period_label}` : ''}
          </p>
          {result.report_context?.dates.from && result.report_context.dates.to && (
            <p className="text-xs text-muted-foreground">
              {result.report_context.dates.from} – {result.report_context.dates.to}
            </p>
          )}
        </div>
        {winner && <Button onClick={() => onOpenReport(job.id)}>Best report</Button>}
      </header>
      <dl className="grid grid-cols-2 gap-5 border-y py-5 sm:grid-cols-4">
        {[
          [
            'Proposals',
            `${number(counts.proposed)} / ${number(experiment.search_space?.proposal_budget ?? experiment.specification.trials)}`,
          ],
          ['Distinct portfolios', number(counts.distinct)],
          ['Repeated proposals', number(counts.reused)],
          ['Best objective score', number(winner?.score)],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-2 text-xl font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <Tabs
        value={view.section}
        onValueChange={(section) => setView({ ...view, section: section as typeof view.section })}
      >
        <TabsList className="mb-5 flex w-fit max-w-full flex-wrap h-auto">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="parameters">Parameters</TabsTrigger>
          <TabsTrigger value="trials">Trials</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="space-y-7">
          <AnalysisPreparation {...actions} missing={!native} />
          <div className="grid min-w-0 gap-8 xl:grid-cols-2">
            {chart('history')}
            {figure(scatter)}
          </div>
          <p className="text-xs text-muted-foreground">
            Select a plotted trial to inspect it. Return and drawdown show distinct portfolios.
          </p>
        </TabsContent>
        <TabsContent value="parameters" className="space-y-7">
          <AnalysisPreparation {...actions} missing={!native} />
          {native && (
            <>
              <div className="flex flex-wrap items-end gap-3">
                {axes.length > 1 &&
                  [0, 1].map((index) => (
                    <label className="min-w-0 space-y-2 text-xs text-muted-foreground" key={index}>
                      <span className="block">Parameter {index + 1}</span>
                      <select
                        className="h-9 max-w-full rounded-md border bg-background px-3 text-sm text-foreground"
                        aria-label={`Study parameter ${index + 1}`}
                        value={selected[index] ?? ''}
                        disabled={analysis.busy || readOnly}
                        onChange={(event) => {
                          const next = [...selected]
                          next[index] = event.target.value
                          setView({ ...view, parameters: next })
                        }}
                      >
                        {axes.map((key) => (
                          <option key={key} value={key} disabled={key === selected[1 - index]}>
                            {labels[key]}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                {axes.length > 1 && !readOnly && (
                  <Button
                    variant="outline"
                    disabled={analysis.busy || selected.length !== 2 || selected[0] === selected[1]}
                    onClick={() => analysis.prepare(selected)}
                  >
                    Update charts
                  </Button>
                )}
              </div>
              <div className="grid min-w-0 gap-8 xl:grid-cols-2">
                {chart('importance')}
                {chart('slice')}
              </div>
              {chart('contour', true)}
              <p className="text-xs text-muted-foreground">
                Importance explains the objective across all proposals. On the contour, select an
                observed point; the shaded surface is an estimate.
              </p>
              <div className="border-t pt-4">
                <Button
                  variant="ghost"
                  aria-expanded={view.advanced}
                  onClick={() => setView({ ...view, advanced: !view.advanced })}
                >
                  {view.advanced ? 'Hide' : 'Show'} advanced charts
                </Button>
                {view.advanced && (
                  <div className="space-y-8 pt-5">
                    {chart('parallel', true)}
                    {chart('rank', true)}
                    {chart('edf', true)}
                  </div>
                )}
              </div>
              <AnalysisBasis analysis={native} />
            </>
          )}
        </TabsContent>
        <TabsContent value="trials">
          <PortfolioTrials
            experiment={experiment}
            page={view.trials.page}
            onPageChange={(page) => setView({ ...view, trials: { ...view.trials, page } })}
            view={view.trials}
            onViewChange={(trials) => setView({ ...view, trials })}
            onInspect={inspect}
            onReport={showReport}
            reportAction={reportAction}
            onRerun={onRerun}
            rerunning={rerunning}
            readOnly={readOnly}
            renderSettings={renderSettings}
          />
        </TabsContent>
        <TabsContent value="activity" className="space-y-5">
          {view.section === 'activity' && (
            <StudyActivity jobId={job.id} jobStatus={job.status} strategies={result.strategies} />
          )}
          {chart('timeline')}
        </TabsContent>
      </Tabs>
      <Dialog
        open={Boolean(candidate) || overlapping.length > 0}
        onOpenChange={(open) => {
          if (!open) {
            setOverlapping([])
            setView({ ...view, candidate: null })
          }
        }}
      >
        <DialogContent
          className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl"
          onCloseAutoFocus={(event) => {
            if (opener.current?.isConnected) {
              event.preventDefault()
              opener.current.focus()
            }
          }}
        >
          {overlapping.length > 1 ? (
            <>
              <DialogHeader>
                <DialogTitle>Trials at this point</DialogTitle>
                <DialogDescription>
                  These proposals share the plotted settings. Choose one to inspect.
                </DialogDescription>
              </DialogHeader>
              <div className="divide-y">
                {overlapping.map((point) => (
                  <Button
                    key={point.proposalNumber}
                    variant="ghost"
                    className="flex w-full justify-between"
                    onClick={() => inspect(point.configId, point.proposalNumber)}
                  >
                    Trial {point.proposalNumber + 1}
                    <span>
                      Score{' '}
                      {number(
                        experiment.trials?.find((item) => item.number === point.proposalNumber)
                          ?.value
                      )}
                    </span>
                  </Button>
                ))}
              </div>
            </>
          ) : candidate && row ? (
            <>
              <DialogHeader>
                <DialogTitle>
                  Trial {candidate.proposalNumber + 1}
                  {row.config_id === experiment.recommendation_id ? ' · Best by objective' : ''}
                </DialogTitle>
                <DialogDescription>
                  {proposal?.reused
                    ? `Repeated proposal · uses the portfolio first tested in Trial ${row.trial_number + 1}. `
                    : ''}
                  {result.report_context?.period_label ?? 'Study period'} ·{' '}
                  {experiment.optimizer.objective_definition}
                </DialogDescription>
              </DialogHeader>
              <dl className="grid grid-cols-2 gap-4 border-y py-4 sm:grid-cols-4">
                {availableTrialMetrics([row], experiment.analysis_catalog)
                  .filter((metric) =>
                    [
                      'objective_score',
                      'net_return_pct',
                      'max_drawdown_pct',
                      'closed_trades',
                    ].includes(metric.key)
                  )
                  .map((metric) => (
                    <div key={metric.key}>
                      <dt className="text-xs text-muted-foreground" title={metric.description}>
                        {metric.label}
                      </dt>
                      <dd className="mt-1 font-medium tabular-nums">
                        {trialMetricText(metric, trialMetricValue(metric, row))}
                      </dd>
                    </div>
                  ))}
              </dl>
              <div className="flex flex-wrap items-center gap-3">
                {experimentId && (
                  <SaveToShortlist
                    experimentId={experimentId}
                    jobId={job.id}
                    configId={candidate.configId}
                    proposalNumber={candidate.proposalNumber}
                    readOnly={readOnly}
                  />
                )}
                {report?.status === 'ready' && report.report_job_id && (
                  <Button onClick={() => onOpenReport(report.report_job_id!)}>Open report</Button>
                )}
                {report?.status === 'available' && !readOnly && (
                  <Button disabled={preparing} onClick={() => void prepare()}>
                    {preparing ? 'Preparing report…' : 'Prepare report'}
                  </Button>
                )}
                {(report?.status === 'failed' || report?.status === 'mismatch') &&
                  report.report_job_id && (
                    <Button variant="outline" onClick={() => onOpenReport(report.report_job_id!)}>
                      View run
                    </Button>
                  )}
                {onAdjust && !readOnly && (
                  <Button
                    variant="outline"
                    disabled={rerunning}
                    onClick={() => onAdjust(candidate.configId)}
                  >
                    Adjust & test
                  </Button>
                )}
                {(preparing || report?.status === 'queued' || report?.status === 'running') && (
                  <output className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
                    {report?.status === 'running' ? 'Preparing report…' : 'Report queued…'}
                  </output>
                )}
              </div>
              {report?.status === 'available' && (
                <p className="text-xs text-muted-foreground">
                  {readOnly
                    ? 'The full report has not been saved for this trial.'
                    : 'The statistics are saved. Prepare the full report using this trial’s saved settings and prices.'}
                </p>
              )}
              {!report && reports.isPending && (
                <output className="text-xs text-muted-foreground">Checking saved reports…</output>
              )}
              {report?.error && (
                <output className="block text-sm text-destructive">{report.error}</output>
              )}
              {prepareError && (
                <p role="alert" className="text-sm text-destructive">
                  {prepareError}
                </p>
              )}
              {reports.isError && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  Report availability could not be checked.
                  <Button variant="ghost" onClick={() => void reports.refetch()}>
                    Try again
                  </Button>
                </div>
              )}
              <Tabs value={candidateTab} onValueChange={setCandidateTab}>
                <TabsList>
                  <TabsTrigger value="settings">Settings</TabsTrigger>
                  <TabsTrigger value="statistics">Statistics</TabsTrigger>
                </TabsList>
                <TabsContent value="settings">
                  <div className="divide-y">
                    {row.strategies.map((strategy) => (
                      <div key={strategy.id}>{renderSettings(strategy)}</div>
                    ))}
                  </div>
                </TabsContent>
                <TabsContent value="statistics">
                  {row.analysis && experiment.analysis_catalog ? (
                    <AnalysisMetricTable
                      catalog={experiment.analysis_catalog}
                      analysis={row.analysis}
                    />
                  ) : (
                    <p className="py-5 text-sm text-muted-foreground">
                      Extended statistics were not recorded for this trial.
                    </p>
                  )}
                </TabsContent>
              </Tabs>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function BackToStudy({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" className="mb-4 gap-2" onClick={onClick}>
      <ArrowLeft className="size-4" />
      Back to study
    </Button>
  )
}
