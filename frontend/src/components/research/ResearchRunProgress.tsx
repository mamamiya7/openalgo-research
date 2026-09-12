import { Check, ChevronDown, Database, FileSpreadsheet, Flag, FlaskConical } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { PortfolioJob } from '@/api/portfolioResearch'
import type { ResearchActivity } from '@/api/scannerResearch'
import './ResearchRunProgress.css'

type ProgressJob = Pick<
  PortfolioJob,
  'status' | 'kind' | 'activity' | 'queue_position' | 'source_summary' | 'specification'
>
const valid = (value: number | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0
const count = (value: number) => value.toLocaleString('en-IN')
const intervalName = (interval?: string) =>
  interval === 'D' ? 'Daily' : interval === '1m' ? '1-minute' : null

function signalDateRange(source?: ProgressJob['source_summary']) {
  const from = source?.date_from
  const to = source?.date_to
  if (!from || !to || from > to) return undefined
  const dates = [from, to].map((value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined
    const date = new Date(`${value}T00:00:00Z`)
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
      ? date
      : undefined
  })
  const [first, last] = dates
  if (!first || !last) return undefined
  const format = (date: Date, year = true) =>
    new Intl.DateTimeFormat('en-IN', {
      day: 'numeric',
      month: 'short',
      ...(year ? { year: 'numeric' as const } : {}),
      timeZone: 'UTC',
    }).format(date)
  return from === to
    ? format(first)
    : `${format(first, first.getUTCFullYear() !== last.getUTCFullYear())}–${format(last)}`
}

function priceWindowSummary(job: ProgressJob, interval?: string) {
  if (!intervalName(interval)) return undefined
  const portfolio = job.specification?.portfolio
  if (interval === 'D' && portfolio?.strategies.length) {
    const holds = portfolio.strategies.map((strategy) =>
      portfolio.optimization && strategy.search?.hold_sessions
        ? strategy.search.hold_sessions.max
        : strategy.config.hold_sessions
    )
    if (holds.every((value) => Number.isSafeInteger(value) && value > 0)) {
      // Daily plans include the entry session and each later holding session.
      return `Up to ${count(Math.max(...holds) + 1)} trading days per signal · shared candles counted once`
    }
  }
  return 'Entry-to-exit prices · shared candles counted once'
}

function stepIndex(stage?: ResearchActivity['stage']): number {
  if (!stage) return -1
  if (stage === 'csv') return 0
  if (['planning', 'cache', 'download', 'verify'].includes(stage)) return 1
  if (['initializing', 'optimizing', 'backtest', 'validation'].includes(stage)) return 2
  return stage === 'saving' || stage === 'complete' ? 3 : -1
}

function stageTitle(activity?: ResearchActivity, optimizing = false) {
  const interval = intervalName(activity?.prices?.interval)?.toLowerCase()
  switch (activity?.stage) {
    case 'csv':
      return 'Reading signals'
    case 'planning':
      return 'Planning required prices'
    case 'cache':
      return 'Checking prices in OpenAlgo'
    case 'download':
      return `Downloading ${interval ? `${interval} ` : ''}prices`
    case 'verify':
      return 'Verifying saved prices'
    case 'initializing':
      return 'Preparing the calculation engine'
    case 'optimizing':
      return 'Testing parameters with Optuna'
    case 'backtest':
      return 'Running your backtest'
    case 'validation':
      return 'Checking the later period'
    case 'saving':
      return 'Saving results'
    case 'complete':
      return 'Saving results'
    default:
      return optimizing ? 'Running your optimization' : 'Running your backtest'
  }
}

function statusTitle(job: ProgressJob, optimizing: boolean) {
  switch (job.status) {
    case 'running':
      return stageTitle(job.activity, optimizing)
    case 'queued':
      return job.activity?.batch_count ? 'Waiting to continue' : 'Waiting to start'
    case 'cancelling':
    case 'cancel_requested':
      return 'Stopping your run…'
    case 'failed':
      return 'Run stopped'
    case 'interrupted':
      return 'Run interrupted'
    case 'cancelled':
      return 'Run cancelled'
    case 'completed':
      return 'Results ready'
    default:
      return 'Run saved'
  }
}

export function researchRunStatus(job: ProgressJob): string {
  return statusTitle(
    { ...job, activity: job.activity?.version === 1 ? job.activity : undefined },
    job.kind === 'portfolio_optimize'
  )
}

function elapsedText(start: number, end: number) {
  const seconds = Math.max(0, Math.floor(end - start))
  if (seconds < 60) return `${seconds}s elapsed`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s elapsed`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m elapsed`
}

function Elapsed({ activity, running }: { activity?: ResearchActivity; running: boolean }) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  const started = activity?.started_at
  useEffect(() => {
    if (!running || !valid(started)) return
    setNow(Date.now() / 1000)
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [running, started])
  if (!activity || !valid(started) || !valid(activity.updated_at)) return null
  return (
    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
      {elapsedText(started, running ? Math.max(now, activity.updated_at) : activity.updated_at)}
    </span>
  )
}

function Metric({ label, value }: { label: string; value?: number }) {
  if (!valid(value)) return null
  return (
    <div className="research-progress-metric">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-lg font-medium tabular-nums">{count(value)}</dd>
    </div>
  )
}

function StageBar({ label, value, total }: { label: string; value?: number; total?: number }) {
  const known = valid(value) && valid(total) && total > 0
  return (
    <progress
      className="research-stage-progress"
      aria-label={label}
      max={known ? total : undefined}
      value={known ? Math.min(value, total) : undefined}
    />
  )
}

function PriceProgress({
  activity,
  running,
  windowSummary,
}: {
  activity: ResearchActivity
  running: boolean
  windowSummary?: string
}) {
  const prices = activity.prices
  const knownTotal = valid(prices?.required_candles)
  const available = prices?.available_candles
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        {valid(available) && (
          <p className="research-progress-number text-3xl font-semibold tracking-tight tabular-nums">
            {count(available)}
            {knownTotal && (
              <span className="text-base font-normal text-muted-foreground">
                {' '}
                / {count(prices.required_candles!)}
              </span>
            )}
          </p>
        )}
        {valid(available) && (
          <span className="text-sm text-muted-foreground">candles available</span>
        )}
        {!valid(available) && knownTotal && (
          <p className="text-sm text-muted-foreground">
            {count(prices.required_candles!)} candles needed
          </p>
        )}
        {intervalName(prices?.interval) && (
          <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium">
            {intervalName(prices?.interval)}
          </span>
        )}
      </div>
      {windowSummary && <p className="text-xs text-muted-foreground">{windowSummary}</p>}
      {running && (
        <StageBar
          label={knownTotal ? 'Required candles available' : 'Checking required prices'}
          value={knownTotal ? available : undefined}
          total={knownTotal ? prices.required_candles : undefined}
        />
      )}
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Metric label="Already in OpenAlgo" value={prices?.cached_candles} />
        <Metric label="Downloaded" value={prices?.downloaded_candles} />
        <Metric
          label="Still needed"
          value={prices?.cache_complete ? prices.missing_candles : undefined}
        />
      </dl>
      <p className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
        {valid(prices?.checked_symbols) && valid(prices?.total_symbols) && (
          <span>
            {count(prices.checked_symbols)} of {count(prices.total_symbols)} symbols checked
          </span>
        )}
        {running && prices?.current_symbol && <span>· {prices.current_symbol}</span>}
        {running && !prices?.cache_complete && <span>· Checking total coverage…</span>}
      </p>
    </div>
  )
}

function TrialHistory({ history }: { history?: ResearchActivity['trials'] }) {
  const points = (history?.history ?? [])
    .filter((point) => Number.isFinite(point.trial) && Number.isFinite(point.score))
    .slice(-100)
  if (!points.length) return null
  const scores = points.map((point) => point.score)
  const trials = points.map((point) => point.trial)
  const low = Math.min(...scores)
  const high = Math.max(...scores)
  const first = Math.min(...trials)
  const last = Math.max(...trials)
  const coordinates = points.map((point) => [
    4 + ((point.trial - first) / (last - first || 1)) * 392,
    high === low ? 32 : 60 - ((point.score - low) / (high - low)) * 56,
  ])
  const latest = coordinates[coordinates.length - 1]
  return (
    <figure className="space-y-1 border-t pt-3">
      <svg
        role="img"
        aria-label={`Trial score history; latest score ${scores[scores.length - 1].toFixed(3)}`}
        viewBox="0 0 400 64"
        className="research-trial-history h-16 w-full overflow-visible"
        preserveAspectRatio="none"
      >
        <title>Actual trial scores</title>
        <path d="M4 60H396" stroke="currentColor" strokeOpacity="0.12" />
        <polyline
          points={coordinates.map((point) => point.join(',')).join(' ')}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <circle cx={latest[0]} cy={latest[1]} r="3" fill="currentColor" />
      </svg>
      <figcaption className="flex justify-between text-xs text-muted-foreground">
        <span>Trial scores</span>
        <span>Trial {count(points[points.length - 1].trial)}</span>
      </figcaption>
    </figure>
  )
}

function Details({ activity }: { activity: ResearchActivity }) {
  const prices = activity.prices
  const trials = activity.trials
  const rows: Array<[string, number | undefined]> = [
    ['Files processed', activity.inputs?.files],
    ['Signals accepted', activity.inputs?.signals],
    ['Unique symbols', activity.inputs?.symbols],
    ['Rows excluded', activity.inputs?.excluded_rows],
    ['Candles required', prices?.required_candles],
    ['Symbols fully covered', prices?.covered_symbols],
    ['Candles unavailable from broker', prices?.unavailable_candles],
    ['Download windows remaining', prices?.pending_windows],
    ['Unique calculations', trials?.evaluated],
    ['Reused trials', trials?.reused],
    ['Rejected trials', trials?.rejected],
    ['Failed trials', trials?.failed],
  ]
  if (!rows.some(([, value]) => valid(value))) return null
  return (
    <details className="group border-t pt-3 text-xs">
      <summary className="flex w-fit cursor-pointer list-none items-center gap-1 rounded text-muted-foreground outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
        Run details
        <ChevronDown
          className="size-3.5 transition-transform group-open:rotate-180 motion-reduce:transition-none"
          aria-hidden="true"
        />
      </summary>
      <dl className="mt-3 grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2">
        {rows
          .filter(([, value]) => valid(value))
          .map(([label, value]) => (
            <div key={label} className="flex justify-between gap-3">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="tabular-nums">{count(value!)}</dd>
            </div>
          ))}
      </dl>
    </details>
  )
}

export function ResearchRunProgress({ job }: { job: ProgressJob }) {
  const activity = job.activity?.version === 1 ? job.activity : undefined
  const optimizing = job.kind === 'portfolio_optimize'
  const running = job.status === 'running'
  const current = job.status === 'completed' ? 4 : stepIndex(activity?.stage)
  const title = researchRunStatus(job)
  const inputs = activity?.inputs
  const trials = activity?.trials
  const showTrials =
    activity?.stage === 'optimizing' || (activity?.stage === 'initializing' && !!trials)
  const steps = [
    {
      name: 'Read CSV',
      Icon: FileSpreadsheet,
      summary:
        valid(inputs?.signals) && valid(inputs?.symbols)
          ? `${count(inputs.signals)} signals · ${count(inputs.symbols)} symbols`
          : undefined,
      dates: signalDateRange(job.source_summary),
    },
    {
      name: 'Prepare prices',
      Icon: Database,
      summary:
        current > 1 && valid(activity?.prices?.available_candles)
          ? `${count(activity.prices.available_candles)} ${intervalName(activity.prices.interval)?.toLowerCase() ?? ''} candles`
          : undefined,
    },
    {
      name: optimizing ? 'Optimize' : 'Backtest',
      Icon: FlaskConical,
      summary:
        current >= 2 && optimizing && valid(trials?.completed) && valid(trials?.total)
          ? `${count(trials.completed)} / ${count(trials.total)} trials`
          : undefined,
    },
    { name: 'Results', Icon: Flag, summary: current === 4 ? 'Ready' : undefined },
  ]
  return (
    <div
      className="research-run-progress space-y-7"
      data-running={running}
      data-stage={activity?.stage ?? 'unknown'}
    >
      <ol aria-label="Run stages" className="research-stage-track">
        {steps.map(({ name, Icon, summary, dates }, index) => {
          const done = current > index
          return (
            <li
              key={name}
              data-state={done ? 'complete' : current === index ? 'current' : 'waiting'}
              aria-current={current === index ? 'step' : undefined}
              className="research-stage"
            >
              <span className="research-stage-icon" aria-hidden="true">
                {done ? <Check className="size-4" /> : <Icon className="size-4" />}
              </span>
              <div className="min-w-0">
                <span className="text-xs font-medium">{name}</span>
                {summary && <p className="mt-1 text-xs text-muted-foreground">{summary}</p>}
                {dates && <p className="mt-1 text-xs text-muted-foreground">{dates}</p>}
              </div>
            </li>
          )
        })}
      </ol>
      <div className="research-active-stage space-y-5 rounded-2xl border bg-card p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <output
            aria-live="polite"
            aria-atomic="true"
            className="research-stage-title font-medium"
          >
            <span className="research-stage-beacon" aria-hidden="true" />
            {title}
          </output>
          <Elapsed activity={activity} running={running} />
        </div>
        {job.status === 'queued' && (
          <p className="text-sm text-muted-foreground">
            {activity?.batch_count
              ? 'Saved progress will continue automatically.'
              : job.queue_position && job.queue_position > 1
                ? `Position ${job.queue_position} in the queue.`
                : 'Your run is in the queue.'}
          </p>
        )}
        {activity && current === 1 && (
          <PriceProgress
            activity={activity}
            running={running}
            windowSummary={priceWindowSummary(job, activity.prices?.interval)}
          />
        )}
        {activity?.stage === 'csv' && (
          <dl className="grid grid-cols-3 gap-4">
            <Metric label="Files processed" value={inputs?.files} />
            <Metric label="Signals accepted" value={inputs?.signals} />
            <Metric label="Unique symbols" value={inputs?.symbols} />
          </dl>
        )}
        {showTrials && (
          <div className="space-y-4">
            {valid(trials?.completed) && valid(trials?.total) && (
              <p className="research-progress-number text-3xl font-semibold tracking-tight tabular-nums">
                {count(trials.completed)}{' '}
                <span className="text-base font-normal text-muted-foreground">
                  / {count(trials.total)} trials{' '}
                  {valid(trials.failed) && trials.failed > 0 ? 'processed' : 'completed'}
                </span>
              </p>
            )}
            {running && (
              <StageBar
                label={activity?.stage === 'initializing' ? title : 'Optimization trials completed'}
                value={activity?.stage === 'initializing' ? undefined : trials?.completed}
                total={activity?.stage === 'initializing' ? undefined : trials?.total}
              />
            )}
            {running && valid(trials?.active_trial ?? undefined) && (
              <p className="text-xs text-muted-foreground">
                {activity?.stage === 'initializing' ? 'Preparing' : 'Testing'} trial{' '}
                {trials?.active_trial}
              </p>
            )}
            <TrialHistory history={trials} />
          </div>
        )}
        {running && current !== 1 && !showTrials && <StageBar label={title} />}
        {activity?.inputs?.excluded_rows ? (
          <p className="text-xs text-muted-foreground">
            {count(activity.inputs.excluded_rows)} CSV rows excluded · See run details
          </p>
        ) : null}
        {activity?.prices?.unavailable_candles ? (
          <p className="text-xs text-muted-foreground">
            {count(activity.prices.unavailable_candles)} required candles unavailable from the
            broker
          </p>
        ) : null}
        {activity && <Details activity={activity} />}
      </div>
    </div>
  )
}

export interface ResearchUploadActivity {
  total: number
  completed: number
  filename: string
  signals: number
}

export function ResearchUploadProgress({ activity }: { activity: ResearchUploadActivity }) {
  return (
    <div
      className="research-run-progress space-y-2 rounded-lg bg-muted/50 px-4 py-3"
      data-running="true"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <output className="font-medium">
          Reading CSV {activity.completed + 1} of {activity.total}
        </output>
        <span className="max-w-full truncate text-xs text-muted-foreground">
          {activity.filename}
        </span>
      </div>
      <StageBar label="Reading CSV file" />
      {activity.completed > 0 && (
        <p className="text-xs text-muted-foreground">
          {count(activity.completed)} {activity.completed === 1 ? 'file' : 'files'} processed ·{' '}
          {count(activity.signals)} signals accepted
        </p>
      )}
    </div>
  )
}
