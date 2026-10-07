import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import type { PortfolioSettings } from '@/api/portfolioResearch'
import {
  researchStudyActivity,
  type StudyActivityRow,
  type StudyExecution,
  type StudyProposalState,
} from '@/api/researchStudyActivity'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useAuthStore } from '@/stores/authStore'

const active = (status: string) =>
  ['queued', 'running', 'pausing', 'cancelling', 'cancel_requested'].includes(status)
const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)
const number = (value: unknown) =>
  finite(value) ? value.toLocaleString('en-IN', { maximumFractionDigits: 4 }) : '—'
const stateLabels: Record<StudyProposalState | StudyExecution['state'], string> = {
  running: 'Running',
  evaluated: 'Finished',
  completed: 'Completed',
  reused: 'Reused',
  allocation_rejected: 'Allocation excluded',
  failed: 'Failed',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
  paused: 'Paused',
  observation_failed: 'Recording interrupted',
}
const parameterLabels: Record<string, string> = {
  target_pct: 'Profit target',
  stop_pct: 'Stop loss',
  hold_sessions: 'Holding sessions',
  hold_minutes: 'Holding minutes',
  trailing_pct: 'Trailing stop',
  order_size_pct: 'Per-trade size',
  allocation_pct: 'Allocation',
}
const reasons: Record<string, string> = {
  calculation_failed: 'The calculation failed during this attempt.',
  cancellation_requested: 'Cancellation was requested.',
  pause_requested: 'Progress saved at your request.',
  worker_shutdown: 'The calculation worker stopped.',
  worker_lost: 'The calculation worker was no longer available. Its end time was not observed.',
  observation_failed: 'Activity recording stopped. Completion could not be established here.',
}

function timestamp(value: unknown) {
  if (!finite(value)) return 'Not recorded'
  const date = new Date(value * 1000)
  return Number.isFinite(date.getTime())
    ? date.toLocaleString('en-IN', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : 'Not recorded'
}

function duration(row: Pick<StudyActivityRow, 'started_at' | 'finished_at'>) {
  if (!finite(row.started_at) || !finite(row.finished_at) || row.finished_at < row.started_at)
    return '—'
  const seconds = row.finished_at - row.started_at
  if (seconds < 60) return `${seconds.toLocaleString('en-IN', { maximumFractionDigits: 1 })} s`
  const minutes = Math.floor(seconds / 60)
  return `${number(minutes)} min ${number(Math.floor(seconds % 60))} s`
}

interface Props {
  jobId: string
  jobStatus: string
  strategies?: Array<Pick<PortfolioSettings, 'id' | 'name'>>
}

/** Mount only inside a visible Activity surface. Identity changes discard local views and requests. */
export function StudyActivity(props: Props) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  return <Activity key={JSON.stringify([owner, props.jobId])} {...props} owner={owner} />
}

function Activity({ jobId, jobStatus, strategies = [], owner }: Props & { owner: string }) {
  const [before, setBefore] = useState<number>()
  const [execution, setExecution] = useState('')
  const [selected, setSelected] = useState<StudyActivityRow | null>(null)
  const opener = useRef<HTMLButtonElement | null>(null)
  const query = useQuery({
    queryKey: ['research-study-activity', owner, jobId, jobStatus, execution, before],
    queryFn: ({ signal }) =>
      researchStudyActivity.get(jobId, { before, execution: execution || undefined }, signal),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchInterval: (current) =>
      active(jobStatus) && (!current.state.data || active(current.state.data.job_status))
        ? 1500
        : false,
  })
  const data = query.data
  const attempt = execution
    ? data?.executions.find((item) => item.id === execution)
    : data?.executions[0]
  const row = data?.rows.find((item) => item.id === selected?.id) ?? selected
  function setting(key: string, index: number) {
    const [strategyId, parameter] = key.split('.')
    const label = parameterLabels[parameter] ?? `Setting ${index + 1}`
    const strategy = strategies.find((item) => item.id === strategyId)
    return `${label}${strategies.length > 1 && strategy ? ` · ${strategy.name}` : ''}`
  }

  if (query.isPending)
    return <output className="block py-5 text-sm text-muted-foreground">Loading activity…</output>
  if (!data)
    return (
      <div className="flex flex-wrap items-center gap-3 py-4">
        <p role="alert" className="text-sm text-muted-foreground">
          Activity could not be loaded.
        </p>
        <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
          Try again
        </Button>
      </div>
    )
  if (!data.available)
    return (
      <p className="py-4 text-sm text-muted-foreground">
        {active(data.job_status)
          ? 'Trial activity will appear when optimization starts.'
          : 'Detailed activity was not recorded for this study.'}
      </p>
    )

  return (
    <section className="min-w-0 space-y-5" aria-label="Recorded study activity">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h3 className="text-base font-semibold">Recorded activity</h3>
          <p
            className="text-sm text-muted-foreground"
            title="Includes repeated proposals and later attempts. These counts are separate from search progress and distinct portfolios."
          >
            {number(data.counts.recorded)} proposal observations
            {execution ? ' in this attempt' : ' across recorded attempts'}
          </p>
        </div>
        {(data.executions.length > 1 || execution) && (
          <label className="space-y-1 text-xs text-muted-foreground">
            <span className="block">Calculation attempt</span>
            <select
              className="h-9 max-w-full rounded-md border bg-background px-2 text-sm text-foreground"
              value={execution}
              onChange={(event) => {
                setBefore(undefined)
                setExecution(event.target.value)
                setSelected(null)
              }}
            >
              <option value="">All attempts</option>
              {data.executions.map((item) => (
                <option key={item.id} value={item.id}>
                  {timestamp(item.started_at)} · {stateLabels[item.state]}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>
      <dl className="flex flex-wrap gap-x-6 gap-y-2 border-y py-3 text-sm">
        {(Object.keys(stateLabels) as Array<keyof typeof stateLabels>)
          .filter((state): state is StudyProposalState => state in data.counts)
          .filter((state) => data.counts[state] > 0)
          .map((state) => (
            <div className="flex items-center gap-2" key={state}>
              <dt className="text-muted-foreground">{stateLabels[state]}</dt>
              <dd className="font-medium tabular-nums">{number(data.counts[state])}</dd>
            </div>
          ))}
        {data.counts.recorded === 0 && (
          <div className="text-muted-foreground">No proposals recorded yet.</div>
        )}
      </dl>
      {attempt && (
        <div className="space-y-1 text-xs text-muted-foreground">
          <p>
            {execution ? 'Selected' : 'Latest'} attempt · {stateLabels[attempt.state]} · Started{' '}
            {timestamp(attempt.started_at)}
          </p>
          {attempt.state !== 'running' && (
            <p>
              Finished {timestamp(attempt.finished_at)} · Last observed{' '}
              {timestamp(attempt.observed_at)}
            </p>
          )}
          {attempt.reason_code && reasons[attempt.reason_code] && (
            <p>{reasons[attempt.reason_code]}</p>
          )}
        </div>
      )}
      {data.executions_truncated && (
        <p className="text-xs text-muted-foreground">
          The attempt selector lists recent attempts. All attempts includes older recorded activity.
        </p>
      )}
      {query.isError && (
        <output className="block text-xs text-muted-foreground">
          Activity could not refresh. Showing the last loaded observations.
        </output>
      )}
      {data.rows.length > 0 && (
        <div className="max-h-[32rem] overflow-auto rounded-md border">
          <table className="w-full min-w-[660px] text-sm" aria-label="Proposal activity">
            <thead className="sticky top-0 z-10 border-b bg-muted">
              <tr>
                {['Trial', 'Outcome', 'Started', 'Duration', 'Score', 'Settings'].map((label) => (
                  <th scope="col" key={label} className="px-3 py-3 text-left font-medium">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.rows.map((item) => (
                <tr key={item.id} className="hover:bg-muted/40">
                  <th scope="row" className="px-3 py-3 text-left font-medium tabular-nums">
                    Trial {item.number + 1}
                  </th>
                  <td className="px-3 py-3">{stateLabels[item.state]}</td>
                  <td className="whitespace-nowrap px-3 py-3 text-xs text-muted-foreground">
                    {timestamp(item.started_at)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 tabular-nums">{duration(item)}</td>
                  <td className="px-3 py-3 tabular-nums">{number(item.value)}</td>
                  <td className="px-3 py-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(event) => {
                        opener.current = event.currentTarget
                        setSelected(item)
                      }}
                      aria-label={`View settings for Trial ${item.number + 1}`}
                    >
                      View settings
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(before != null || data.next_before != null) && (
        <nav className="flex items-center justify-between gap-3" aria-label="Activity pages">
          <p className="text-xs text-muted-foreground">
            Newest first · Up to 25 observations per page
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={before == null || query.isFetching}
              onClick={() => setBefore(undefined)}
            >
              Latest
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={data.next_before == null || query.isFetching}
              onClick={() => setBefore(data.next_before ?? undefined)}
            >
              Older
            </Button>
          </div>
        </nav>
      )}
      <Dialog open={Boolean(row)} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent
          className="max-h-[85dvh] overflow-y-auto sm:max-w-xl"
          onCloseAutoFocus={(event) => {
            if (opener.current?.isConnected) {
              event.preventDefault()
              opener.current.focus()
            }
          }}
        >
          {row && (
            <>
              <DialogHeader>
                <DialogTitle>Trial {row.number + 1} · Proposed settings</DialogTitle>
                <DialogDescription>Settings recorded when this trial started.</DialogDescription>
              </DialogHeader>
              <dl className="grid grid-cols-2 gap-4 border-y py-4 text-sm">
                {[
                  ['Outcome', stateLabels[row.state]],
                  ['Objective score', finite(row.value) ? String(row.value) : '—'],
                  ['Started', timestamp(row.started_at)],
                  ['Finished', timestamp(row.finished_at)],
                  ['Duration', duration(row)],
                  ['Last observed', timestamp(row.observed_at)],
                ].map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-xs text-muted-foreground">{label}</dt>
                    <dd className="mt-1 tabular-nums">{value}</dd>
                  </div>
                ))}
              </dl>
              {row.reason_code && reasons[row.reason_code] && (
                <p className="text-sm text-muted-foreground">{reasons[row.reason_code]}</p>
              )}
              {row.state !== 'running' && !row.checkpointed && (
                <p className="text-xs text-muted-foreground">
                  This observation is saved. The last recoverable checkpoint may precede it.
                </p>
              )}
              {Object.keys(row.params).length ? (
                <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  {Object.entries(row.params).map(([key, value], index) => (
                    <div key={key}>
                      <dt className="text-xs text-muted-foreground">{setting(key, index)}</dt>
                      <dd className="mt-1 text-sm font-medium tabular-nums">
                        {finite(value) ? String(value) : '—'}
                        {finite(value) && key.endsWith('_pct') ? '%' : ''}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="text-sm text-muted-foreground">No varying settings were recorded.</p>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>
    </section>
  )
}
