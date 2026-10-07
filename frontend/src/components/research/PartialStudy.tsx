import { useQuery } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { useRef, useState } from 'react'
import { type PartialStudyReceipt, researchPartialStudy } from '@/api/researchPartialStudy'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useAuthStore } from '@/stores/authStore'
import { StrategySettings } from './PortfolioResults'
import { trialMetricText } from './portfolioTrialMetrics'

const display = (value: unknown, format: 'percent' | 'number') =>
  trialMetricText({ key: '', label: '', group: '', description: '', format }, value)
const conditionLabels: Record<string, string> = {
  'trend.up': 'Rising Nifty',
  'trend.down': 'Falling Nifty',
  'trend.range': 'Range-bound Nifty',
  'volatility.normal': 'Normal volatility',
  'volatility.high': 'High volatility',
}
const terminal = (status: string) =>
  ['paused', 'interrupted', 'cancelled', 'failed'].includes(status)

/** Visible-only, read-only checkpoint inspection. Account and job changes discard its local state. */
export function PartialStudy(props: { jobId: string; jobStatus: string }) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  if (!terminal(props.jobStatus)) return null
  return (
    <SavedTrials
      key={JSON.stringify([owner, props.jobId, props.jobStatus])}
      {...props}
      owner={owner}
    />
  )
}

function SavedTrials({
  jobId,
  jobStatus,
  owner,
}: {
  jobId: string
  jobStatus: string
  owner: string
}) {
  const [offset, setOffset] = useState(0)
  const [checkpoint, setCheckpoint] = useState<string>()
  const [detail, setDetail] = useState<PartialStudyReceipt['rows'][number] | null>(null)
  const opener = useRef<HTMLButtonElement | null>(null)
  const query = useQuery({
    queryKey: ['research-partial-study', owner, jobId, jobStatus, offset, checkpoint],
    queryFn: ({ signal }) =>
      researchPartialStudy.get(jobId, { offset, expected_checkpoint: checkpoint }, signal),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  const data = query.data
  const checkpointChanged =
    isAxiosError(query.error) &&
    query.error.response?.data?.code === 'partial_study_checkpoint_changed'

  function reset() {
    setOffset(0)
    setCheckpoint(undefined)
    setDetail(null)
    if (offset === 0 && checkpoint === undefined) void query.refetch()
  }
  function page(next: number) {
    if (!data?.checkpoint_artifact) return
    setCheckpoint(data.checkpoint_artifact)
    setOffset(next)
    setDetail(null)
  }

  if (query.isPending)
    return (
      <output className="block py-4 text-sm text-muted-foreground">Loading saved trials…</output>
    )
  if (!data || data.job_id !== jobId || data.status !== jobStatus)
    return (
      <div className="flex flex-wrap items-center gap-3 py-4">
        <p role="alert" className="text-sm text-muted-foreground">
          {checkpointChanged
            ? 'Saved progress changed. Load its latest trials.'
            : 'Saved trials could not be loaded.'}
        </p>
        <Button type="button" variant="outline" size="sm" onClick={reset}>
          {checkpointChanged ? 'Load latest' : 'Try again'}
        </Button>
      </div>
    )
  if (!data.available)
    return (
      <p className="py-4 text-sm text-muted-foreground">
        {data.reason ?? 'No saved trials are available for this study.'}
      </p>
    )

  return (
    <section className="min-w-0 space-y-4" aria-label="Saved study trials">
      <header className="space-y-1">
        <h3 className="font-semibold">Saved trials</h3>
        <p className="text-sm text-muted-foreground">From saved progress · study unfinished</p>
        {data.automatic && (
          <p className="text-xs text-muted-foreground">
            Automatic research ·{' '}
            {data.scope === 'selection'
              ? 'Selection period'
              : data.scope === 'search'
                ? 'Search period'
                : 'Saved study period'}
          </p>
        )}
      </header>
      {data.total_rows === 0 ? (
        <p className="text-sm text-muted-foreground">No evaluated portfolios were saved yet.</p>
      ) : (
        <>
          <section
            className="max-h-[28rem] overflow-auto rounded-lg border"
            // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users must be able to scroll this bounded trial table.
            tabIndex={0}
            aria-label="Saved trial table"
          >
            <table className="w-full text-left text-sm tabular-nums" aria-label="Saved trials">
              <thead className="sticky top-0 bg-muted text-xs text-muted-foreground">
                <tr>
                  {['Trial', 'Return', 'Max drawdown', 'Closed trades', 'Settings'].map((label) => (
                    <th key={label} scope="col" className="whitespace-nowrap px-3 py-3 font-medium">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.config_id} className="border-t">
                    <td className="px-3 py-3">{row.trial_number + 1}</td>
                    <td className="whitespace-nowrap px-3 py-3">
                      {display(row.summary.net_return_pct, 'percent')}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3">
                      {display(row.summary.max_drawdown_pct, 'percent')}
                    </td>
                    <td className="px-3 py-3">{display(row.summary.closed_trades, 'number')}</td>
                    <td className="px-3 py-2">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        aria-label={`View settings for Trial ${row.trial_number + 1}`}
                        onClick={(event) => {
                          opener.current = event.currentTarget
                          setDetail(row)
                        }}
                      >
                        View settings
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {data.offset + 1}–{data.offset + data.rows.length} of{' '}
              {data.total_rows.toLocaleString('en-IN')} saved portfolios
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={data.offset === 0}
                onClick={() => page(Math.max(0, data.offset - data.limit))}
              >
                Previous
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={data.next_offset === null}
                onClick={() => {
                  if (data.next_offset !== null) page(data.next_offset)
                }}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
      <Dialog
        open={detail !== null}
        onOpenChange={(open) => {
          if (!open) setDetail(null)
        }}
      >
        <DialogContent
          className="max-h-[85vh] overflow-y-auto sm:max-w-2xl"
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            if (opener.current?.isConnected) opener.current.focus()
          }}
        >
          <DialogHeader>
            <DialogTitle>
              Trial {detail ? detail.trial_number + 1 : ''} · Saved settings
            </DialogTitle>
            <DialogDescription>Settings from this saved evaluation.</DialogDescription>
          </DialogHeader>
          {detail?.strategies.map((strategy) => (
            <div key={strategy.id}>
              <StrategySettings strategy={strategy} />
              {strategy.entry_condition && (
                <p className="text-sm">
                  Entry condition:{' '}
                  {'version' in strategy.entry_condition ? strategy.entry_condition.hypothesis.label : conditionLabels[
                    `${strategy.entry_condition.dimension}.${strategy.entry_condition.regime}`
                  ] ?? strategy.entry_condition.regime}
                </p>
              )}
            </div>
          ))}
        </DialogContent>
      </Dialog>
    </section>
  )
}
