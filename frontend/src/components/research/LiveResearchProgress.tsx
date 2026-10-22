import { useQuery } from '@tanstack/react-query'
import {
  type LiveResearchMetrics,
  type LiveResearchTrial,
  researchLiveProgress,
} from '@/api/researchLiveProgress'
import { useAuthStore } from '@/stores/authStore'
import { researchDates } from './researchPresentation'

const running = (status: string) => ['running', 'pausing'].includes(status)
const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)
const number = (value: unknown, suffix = '') =>
  finite(value) ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}` : '—'
const stages: Record<string, string> = {
  family_screening: 'Comparing research methods',
  joint_refinement: 'Refining settings',
  checking: 'Checking candidates',
  preparing: 'Preparing research',
  completed: 'Research completed',
}
interface Props {
  jobId: string
  jobStatus: string
  recipeVersion?: string
  /** Completed studies get one read only when their Activity surface is open. */
  allowCompleted?: boolean
}

export function LiveResearchProgress(props: Props) {
  const owner = useAuthStore((state) => state.user?.username)
  if (
    !owner ||
    !/^automatic-hypotheses-v(?:8|9|1[0-6])$/.test(props.recipeVersion ?? '') ||
    (!running(props.jobStatus) &&
      props.jobStatus !== 'paused' &&
      !(props.allowCompleted && props.jobStatus === 'completed'))
  )
    return null
  return (
    <RecordedProgress
      key={JSON.stringify([owner, props.jobId, props.jobStatus])}
      {...props}
      owner={owner}
    />
  )
}

function RecordedProgress({ jobId, jobStatus, owner, allowCompleted }: Props & { owner: string }) {
  const query = useQuery({
    queryKey: ['research-live-progress', owner, jobId, jobStatus],
    queryFn: async ({ signal }) => {
      const receipt = await researchLiveProgress.get(jobId, signal)
      if (receipt.version !== 'research-live-progress-v1' || receipt.job_id !== jobId)
        throw new Error('Live progress does not match this research run.')
      return receipt
    },
    retry: false,
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchInterval: (current) =>
      running(jobStatus) && (!current.state.data || running(current.state.data.job_status))
        ? 5000
        : false,
  })
  const receipt = query.data
  if (
    receipt &&
    !running(receipt.job_status) &&
    receipt.job_status !== 'paused' &&
    !(allowCompleted && receipt.job_status === 'completed')
  )
    return null
  const facts = receipt?.available ? receipt : undefined
  // Price preparation already has its own live counters. Introduce this view
  // only when a committed trial exists, without an empty competing panel.
  if (query.isPending || (!facts && receipt?.stage === 'preparing' && !query.isError)) return null
  const rows: Array<[string, LiveResearchMetrics | LiveResearchTrial | null]> = [
    ['Latest completed', facts?.latest ?? null],
    ['Best development score', facts?.best ?? null],
    ...(facts?.baseline
      ? [['Original settings', facts.baseline] as [string, LiveResearchMetrics]]
      : []),
  ]
  return (
    <section
      aria-label="Live research progress"
      className="space-y-3 rounded-xl border bg-card p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">{facts?.fit?.label ?? 'Live research'}</h3>
          {facts?.fit?.dates && (
            <p className="text-xs text-muted-foreground">
              {researchDates(facts.fit.dates.from, facts.fit.dates.to)}
            </p>
          )}
        </div>
        {facts?.fit && (
          <p className="text-sm tabular-nums">
            {number(facts.fit.completed)} of {number(facts.fit.budget)} trials saved
          </p>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Provisional development results — not a recommendation.
      </p>
      {facts ? (
        <>
          <p className="text-xs text-muted-foreground">{stages[facts.stage] ?? facts.stage}</p>
          <div className="overflow-x-auto">
            <table
              className="w-full text-left text-xs tabular-nums"
              aria-label="Recorded development metrics"
            >
              <thead>
                <tr className="text-muted-foreground">
                  <th className="py-1 pr-3 font-normal">Result</th>
                  <th className="px-2 py-1 text-right font-normal">Return</th>
                  <th className="px-2 py-1 text-right font-normal">Drawdown</th>
                  <th className="px-2 py-1 text-right font-normal">Closed trades</th>
                  <th className="py-1 pl-2 text-right font-normal">Score</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(([label, metrics]) => (
                  <tr key={label} className="border-t border-border/50">
                    <th scope="row" className="whitespace-nowrap py-2 pr-3 font-medium">
                      {label}
                      {metrics && 'trial_number' in metrics && finite(metrics.trial_number) && (
                        <span className="block font-normal text-muted-foreground">
                          Trial {metrics.trial_number + 1}
                        </span>
                      )}
                    </th>
                    <td className="px-2 py-2 text-right">{number(metrics?.return_pct, '%')}</td>
                    <td className="px-2 py-2 text-right">{number(metrics?.drawdown_pct, '%')}</td>
                    <td className="px-2 py-2 text-right">{number(metrics?.closed_trades)}</td>
                    <td className="py-2 pl-2 text-right">{number(metrics?.score)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {facts.events.length > 0 && (
            <ul
              aria-label="Recorded research updates"
              className="space-y-1 text-xs text-muted-foreground"
            >
              {facts.events.slice(-3).map((event, index) => (
                <li key={`${event.kind}-${index}`}>{event.text}</li>
              ))}
            </ul>
          )}
          <p className="text-xs text-muted-foreground">
            {finite(facts.pace.seconds_per_trial) && facts.pace.seconds_per_trial > 0
              ? `Measured pace: ${number(facts.pace.seconds_per_trial)} s per evaluated trial in this fit.`
              : jobStatus === 'completed'
                ? 'No measured pace recorded for this fit.'
                : 'Timing this fit…'}
          </p>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          {query.isError
            ? 'Live figures are temporarily unavailable.'
            : (receipt?.reason ?? 'Waiting for a saved research checkpoint.')}
        </p>
      )}
      {query.isError && facts && (
        <p className="text-xs text-muted-foreground">
          Updates unavailable; showing the last recorded checkpoint.
        </p>
      )}
    </section>
  )
}
