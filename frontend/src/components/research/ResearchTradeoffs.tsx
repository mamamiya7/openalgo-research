import { useQuery } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { useState } from 'react'
import { researchTradeoffs, type TradeoffMetrics } from '@/api/researchTradeoffs'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/authStore'

const roles: Record<string, string> = {
  balanced: 'Balanced',
  best_return: 'Higher return',
  lower_drawdown: 'Lower drawdown',
  fewer_trades: 'Fewer trades',
  fewer_closed_trades: 'Fewer trades',
}
const number = (value: number) =>
  Number.isFinite(value) ? value.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '—'

export function ResearchTradeoffs(props: {
  jobId: string
  resultArtifact?: string
  onInspect: (configId: string) => void
}) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  if (!props.resultArtifact || !/^[a-f0-9]{64}$/.test(props.resultArtifact)) return null
  return (
    <TradeoffsDisclosure
      key={JSON.stringify([owner, props.jobId, props.resultArtifact])}
      {...props}
      owner={owner}
      resultArtifact={props.resultArtifact}
    />
  )
}

function TradeoffsDisclosure(props: {
  jobId: string
  resultArtifact: string
  owner: string
  onInspect: (configId: string) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <section className="space-y-3" aria-label="Development alternatives">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-expanded={open}
        aria-controls={`tradeoffs-${props.jobId}`}
        onClick={() => setOpen(!open)}
      >
        {open ? 'Hide alternatives' : 'Compare alternatives'}
      </Button>
      {open && (
        <div id={`tradeoffs-${props.jobId}`}>
          <RecordedTradeoffs {...props} />
        </div>
      )}
    </section>
  )
}

function RecordedTradeoffs({
  jobId,
  resultArtifact,
  owner,
  onInspect,
}: {
  jobId: string
  resultArtifact: string
  owner: string
  onInspect: (configId: string) => void
}) {
  const query = useQuery({
    queryKey: ['research-tradeoffs', owner, jobId, resultArtifact],
    queryFn: ({ signal }) => researchTradeoffs.get(jobId, resultArtifact, signal),
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  if (query.isPending) return <output>Loading saved alternatives…</output>
  if (query.isError) {
    const changed = isAxiosError(query.error) && query.error.response?.status === 409
    return (
      <div className="space-y-2 text-sm">
        <p role="alert">
          {changed
            ? 'Saved results changed. Reopen this study to compare alternatives.'
            : 'Alternatives could not be loaded.'}
        </p>
        {!changed && (
          <Button type="button" variant="outline" size="sm" onClick={() => query.refetch()}>
            Try again
          </Button>
        )}
      </div>
    )
  }
  const view = query.data
  if (view.job_id !== jobId || view.result_artifact !== resultArtifact)
    return <p role="alert">Saved results changed. Reopen this study to compare alternatives.</p>
  if (!view.available || !view.analysis)
    return (
      <p className="text-sm text-muted-foreground">
        {view.reason ?? 'No comparable saved alternatives are available.'}
      </p>
    )
  const analysis = view.analysis
  function metrics(values: TradeoffMetrics) {
    return (
      <>
        <td className="p-3 text-right">{number(values.net_return_pct)}%</td>
        <td className="p-3 text-right">{number(values.max_drawdown_pct)}%</td>
        <td className="p-3 text-right">{number(values.closed_trades)}</td>
      </>
    )
  }
  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted-foreground">
        Development dates only. These are research alternatives; the recommendation remains
        unchanged.
      </p>
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-left tabular-nums" aria-label="Saved development trade-offs">
          <thead className="bg-muted">
            <tr>
              <th className="p-3 font-medium">Choice</th>
              <th className="p-3 text-right font-medium">Return</th>
              <th className="p-3 text-right font-medium">Max drawdown</th>
              <th className="p-3 text-right font-medium">Closed trades</th>
              <th className="p-3">
                <span className="sr-only">Inspect settings</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {analysis.baseline?.metrics && (
              <tr className="border-t">
                <th scope="row" className="p-3 font-normal">
                  Unchanged reference
                </th>
                {metrics(analysis.baseline.metrics)}
                <td />
              </tr>
            )}
            {analysis.choices.map((choice) => (
              <tr key={choice.config_id} className="border-t">
                <th scope="row" className="p-3 font-normal">
                  {choice.roles.map((role) => roles[role] ?? role).join(' · ')}
                  {choice.selected_existing && ' · Selected'}
                </th>
                {metrics(choice.metrics)}
                <td className="p-3">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={`Inspect ${choice.roles.map((role) => roles[role] ?? role).join(', ')} settings`}
                    onClick={() => onInspect(choice.config_id)}
                  >
                    Inspect
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {analysis.choices.length === 0 && (
        <p>
          {analysis.counts.eligible > 0
            ? 'No eligible alternative improves on the unchanged reference across these measures.'
            : 'No saved candidate passed the declared development support and comparison checks.'}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        {analysis.counts.frontier} non-dominated choices from {analysis.counts.eligible} eligible
        accounts. Trade counts are not turnover, liquidity or confidence.
      </p>
    </div>
  )
}
