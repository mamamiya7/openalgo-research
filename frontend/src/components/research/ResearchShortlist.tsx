import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpRight, Bookmark, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { researchCandidates } from '@/api/researchCandidates'
import {
  isShortlistConflict,
  researchShortlist,
  type ShortlistCandidate,
  type ShortlistDetail,
  shortlistError,
  shortlistKey,
} from '@/api/researchShortlist'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { useAuthStore } from '@/stores/authStore'
import { AnalysisMetricTable } from './AnalysisMetricTable'
import { StrategySettings } from './PortfolioResults'

const number = (value: unknown, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`
    : '—'
const pending = (status: string) => status === 'queued' || status === 'running'
const period = (candidate: ShortlistCandidate) =>
  candidate.period === 'selection' ? 'Selection period' : 'Full period'
const origin = (candidate: ShortlistCandidate) =>
  candidate.origin_kind === 'study'
    ? `Trial ${(candidate.proposal_number ?? candidate.trial_number ?? 0) + 1}`
    : 'Backtest'
const status = (candidate: ShortlistCandidate) =>
  candidate.report.status === 'ready'
    ? 'Report saved'
    : pending(candidate.report.status)
      ? 'Preparing report…'
      : candidate.report.status === 'available'
        ? 'Statistics saved'
        : 'View details'

interface Props {
  experimentId: string
  readOnly: boolean
  onOpenReport: (jobId: string, candidate: ShortlistCandidate) => void
}
export function ResearchShortlist(props: Props) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  return <Shortlist key={`${owner}:${props.experimentId}`} {...props} owner={owner} />
}
function Shortlist({ experimentId, readOnly, onOpenReport, owner }: Props & { owner: string }) {
  const [params, setParams] = useSearchParams()
  const rawOffset = Number(params.get('shortlist_offset') ?? 0)
  const offset =
    Number.isSafeInteger(rawOffset) && rawOffset >= 0 && rawOffset <= 100000 ? rawOffset : 0
  const selected = params.get('shortlist')
  const opener = useRef<HTMLElement | null>(null)
  const list = useQuery({
    queryKey: [...shortlistKey(owner, experimentId), 'page', offset, readOnly],
    queryFn: ({ signal }) => researchShortlist.list(experimentId, { offset }, signal),
    retry: false,
    staleTime: 0,
    gcTime: 0,
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => pending(item.report.status)) ? 1500 : false,
  })
  function choose(id: string | null) {
    const next = new URLSearchParams(params)
    if (id) {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      next.set('shortlist', id)
    } else {
      if (!opener.current?.isConnected && selected)
        opener.current = document.getElementById(`shortlist-open-${selected}`)
      next.delete('shortlist')
    }
    setParams(next)
  }
  function page(nextOffset: number) {
    const next = new URLSearchParams(params)
    next.set('shortlist_offset', String(nextOffset))
    next.delete('shortlist')
    setParams(next)
  }
  return (
    <section className="space-y-5" aria-label="Saved shortlist">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-lg font-semibold">Shortlist</h2>
        {list.data && (
          <span className="text-xs text-muted-foreground">{list.data.total} saved</span>
        )}
      </div>
      {list.isPending && <p className="py-8 text-sm text-muted-foreground">Loading shortlist…</p>}
      {list.isError && (
        <div className="flex items-center gap-3">
          <p role="alert" className="text-sm">
            The shortlist could not be loaded.
          </p>
          <Button variant="outline" size="sm" onClick={() => void list.refetch()}>
            Try again
          </Button>
        </div>
      )}
      {list.data && list.data.items.length === 0 && (
        <div className="space-y-3 py-12 text-center text-muted-foreground">
          <Bookmark className="mx-auto size-6" />
          <p className="text-sm">
            {offset ? 'No more saved candidates.' : 'Save a trial or backtest to keep it here.'}
          </p>
          {offset > 0 && (
            <Button size="sm" variant="outline" onClick={() => page(0)}>
              Back to first page
            </Button>
          )}
        </div>
      )}
      {Boolean(list.data?.items.length) && (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <caption className="sr-only">Saved candidates and their original results</caption>
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                {['Candidate', 'Return', 'Max drawdown', 'Closed trades', 'Report'].map((label) => (
                  <th
                    key={label}
                    scope="col"
                    className="whitespace-nowrap px-4 py-3 text-left font-medium"
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {list.data?.items.map((item) => (
                <tr key={item.id}>
                  <th scope="row" className="min-w-56 max-w-sm px-4 py-4 text-left font-normal">
                    <button
                      id={`shortlist-open-${item.id}`}
                      type="button"
                      className="break-words text-left font-medium underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:outline focus-visible:outline-ring"
                      onClick={() => choose(item.id)}
                    >
                      {item.name}
                    </button>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {origin(item)} · {period(item)}
                      {item.is_objective_winner ? ' · Best by objective' : ''}
                    </p>
                  </th>
                  <td className="whitespace-nowrap px-4 py-4 tabular-nums">
                    {number(item.snapshot.summary.net_return_pct, '%')}
                  </td>
                  <td className="whitespace-nowrap px-4 py-4 tabular-nums">
                    {number(item.snapshot.summary.max_drawdown_pct, '%')}
                  </td>
                  <td className="whitespace-nowrap px-4 py-4 tabular-nums">
                    {number(item.snapshot.summary.closed_trades)}
                  </td>
                  <td className="whitespace-nowrap px-4 py-4 text-xs text-muted-foreground">
                    {status(item)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {list.data && (offset > 0 || list.data.next_offset != null) && (
        <nav aria-label="Shortlist pages" className="flex items-center justify-end gap-3">
          <span className="text-xs text-muted-foreground">
            {Math.min(offset + 1, list.data.total)}–
            {Math.min(offset + list.data.items.length, list.data.total)} of {list.data.total}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={offset === 0}
            onClick={() => page(Math.max(0, offset - 20))}
          >
            Previous
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={list.data.next_offset == null}
            onClick={() => page(list.data!.next_offset!)}
          >
            Next
          </Button>
        </nav>
      )}
      <Dialog
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) choose(null)
        }}
      >
        <DialogContent
          className="max-h-[88vh] overflow-y-auto sm:max-w-3xl"
          onCloseAutoFocus={(event) => {
            if (opener.current?.isConnected) {
              event.preventDefault()
              opener.current.focus()
            }
          }}
        >
          {selected && (
            <CandidateDetail
              key={`${owner}:${experimentId}:${selected}`}
              owner={owner}
              experimentId={experimentId}
              id={selected}
              readOnly={readOnly || Boolean(list.data?.archived)}
              onRemoved={() => choose(null)}
              onOpenReport={onOpenReport}
            />
          )}
        </DialogContent>
      </Dialog>
    </section>
  )
}

function CandidateDetail({
  owner,
  experimentId,
  id,
  readOnly,
  onRemoved,
  onOpenReport,
}: {
  owner: string
  experimentId: string
  id: string
  readOnly: boolean
  onRemoved: () => void
  onOpenReport: Props['onOpenReport']
}) {
  const client = useQueryClient()
  const prefix = shortlistKey(owner, experimentId)
  const key = [...prefix, 'detail', id, readOnly]
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => researchShortlist.get(experimentId, id, signal),
    retry: false,
    staleTime: 0,
    gcTime: 0,
    refetchInterval: (query) =>
      query.state.data && pending(query.state.data.report.status) ? 1500 : false,
  })
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [revision, setRevision] = useState(0)
  const [removing, setRemoving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  const data = detail.data
  const candidate = data?.candidate
  const locked = readOnly || Boolean(data?.archived)
  function edit(value: ShortlistCandidate) {
    setName(value.name)
    setNote(value.note)
    setRevision(value.revision)
    setEditing(true)
    setConflict(false)
    setError(null)
  }
  async function action(fn: (signal: AbortSignal) => Promise<void>) {
    if (request.current || locked) return
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError(null)
    setConflict(false)
    try {
      await fn(controller.signal)
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(shortlistError(cause))
        setConflict(isShortlistConflict(cause))
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false)
      if (request.current === controller) request.current = null
    }
  }
  async function refresh() {
    await client.invalidateQueries({ queryKey: prefix })
  }
  return (
    <>
      <DialogHeader>
        <DialogTitle className="break-words pr-6">
          {candidate?.name ?? 'Saved candidate'}
        </DialogTitle>
        <DialogDescription>
          {candidate
            ? `${origin(candidate)} · ${period(candidate)}${candidate.is_objective_winner ? ' · Best by objective' : ''}`
            : 'Loading saved evidence…'}
        </DialogDescription>
      </DialogHeader>
      {detail.isError && (
        <div className="space-y-3">
          <p role="alert" className="text-sm">
            Saved details could not be loaded.
          </p>
          <Button variant="outline" onClick={() => void detail.refetch()}>
            Try again
          </Button>
        </div>
      )}
      {candidate && data && (
        <>
          <p className="text-xs text-muted-foreground">
            {[candidate.snapshot.dates.from, candidate.snapshot.dates.to]
              .filter(Boolean)
              .join(' – ')}
          </p>
          <dl className="grid grid-cols-2 gap-4 border-y py-4 sm:grid-cols-4">
            {[
              ['Return', number(candidate.snapshot.summary.net_return_pct, '%')],
              ['Max drawdown', number(candidate.snapshot.summary.max_drawdown_pct, '%')],
              ['Closed trades', number(candidate.snapshot.summary.closed_trades)],
              ...(candidate.snapshot.objective
                ? [['Objective score', number(candidate.snapshot.objective.score)]]
                : [
                    [
                      'Starting capital',
                      `${number(candidate.snapshot.capital)}${candidate.snapshot.currency ? ` ${candidate.snapshot.currency}` : ''}`,
                    ],
                  ]),
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="mt-1 font-medium tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          {!editing && !removing && (
            <>
              {candidate.note && (
                <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
                  {candidate.note}
                </p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                {data.report.status === 'ready' && data.report.report_job_id && (
                  <Button onClick={() => onOpenReport(data.report.report_job_id!, candidate)}>
                    Open report
                    <ArrowUpRight className="ml-2 size-4" />
                  </Button>
                )}
                {data.report.status === 'available' && !locked && (
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void action(async (signal) => {
                        await researchCandidates.prepare(
                          candidate.source_job_id,
                          candidate.config_id,
                          signal
                        )
                        if (!signal.aborted) await refresh()
                      })
                    }
                  >
                    {busy ? 'Preparing…' : 'Prepare report'}
                  </Button>
                )}
                {(data.report.status === 'failed' || data.report.status === 'mismatch') &&
                  data.report.report_job_id && (
                    <Button
                      variant="outline"
                      onClick={() => onOpenReport(data.report.report_job_id!, candidate)}
                    >
                      View run
                    </Button>
                  )}
                {!locked && (
                  <>
                    <Button variant="outline" disabled={busy} onClick={() => edit(candidate)}>
                      Edit name & note
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={busy}
                      onClick={() => {
                        setRevision(candidate.revision)
                        setRemoving(true)
                        setError(null)
                      }}
                    >
                      Remove
                    </Button>
                  </>
                )}
                {pending(data.report.status) && (
                  <output className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
                    Preparing report…
                  </output>
                )}
              </div>
            </>
          )}
          {editing && (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault()
                void action(async (signal) => {
                  const updated = await researchShortlist.update(
                    experimentId,
                    id,
                    { revision, name, note },
                    signal
                  )
                  if (signal.aborted) return
                  client.setQueryData<ShortlistDetail>(key, (prior) =>
                    prior ? { ...prior, candidate: updated } : prior
                  )
                  setEditing(false)
                  await refresh()
                })
              }}
            >
              <div className="space-y-1">
                <Label htmlFor="shortlist-name">Name</Label>
                <Input
                  id="shortlist-name"
                  value={name}
                  maxLength={120}
                  disabled={busy || locked}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="shortlist-note">Note</Label>
                <Textarea
                  id="shortlist-note"
                  value={note}
                  maxLength={2000}
                  rows={3}
                  disabled={busy || locked}
                  onChange={(event) => setNote(event.target.value)}
                />
              </div>
              <div className="flex gap-2">
                <Button type="submit" disabled={busy || locked || !name.trim()}>
                  {busy ? 'Saving…' : 'Save changes'}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    setEditing(false)
                    setError(null)
                    setConflict(false)
                  }}
                >
                  Cancel
                </Button>
              </div>
            </form>
          )}
          {removing && (
            <div className="space-y-3 rounded-md border p-4">
              <p className="text-sm">
                Remove from shortlist? The study and its saved results stay available.
              </p>
              <div className="flex gap-2">
                <Button
                  variant="destructive"
                  disabled={busy || locked}
                  onClick={() =>
                    void action(async (signal) => {
                      await researchShortlist.remove(experimentId, id, revision, signal)
                      if (signal.aborted) return
                      onRemoved()
                      await refresh()
                    })
                  }
                >
                  Remove from shortlist
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    setRemoving(false)
                    setError(null)
                    setConflict(false)
                  }}
                >
                  Keep
                </Button>
              </div>
            </div>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {conflict && (
            <Button
              variant="outline"
              onClick={async () => {
                const latest = await detail.refetch()
                if (latest.data) {
                  if (editing) edit(latest.data.candidate)
                  setRemoving(false)
                  setConflict(false)
                  setError(null)
                }
              }}
            >
              Reload saved details
            </Button>
          )}
          {!data.available && (
            <p className="text-sm text-muted-foreground">
              {data.error ??
                candidate.source_error ??
                'The original evidence is unavailable. Its saved statistics remain here.'}
            </p>
          )}
          {data.report.error && data.available && (
            <p className="text-sm text-muted-foreground">{data.report.error}</p>
          )}
          {data.available && (
            <Tabs defaultValue="settings">
              <TabsList>
                <TabsTrigger value="settings">Settings</TabsTrigger>
                <TabsTrigger value="statistics">Statistics</TabsTrigger>
              </TabsList>
              <TabsContent value="settings">
                <div className="divide-y">
                  {data.strategies?.map((strategy) => (
                    <StrategySettings key={strategy.id} strategy={strategy} />
                  ))}
                </div>
              </TabsContent>
              <TabsContent value="statistics">
                {data.analysis && data.analysis_catalog ? (
                  <AnalysisMetricTable analysis={data.analysis} catalog={data.analysis_catalog} />
                ) : (
                  <p className="py-5 text-sm text-muted-foreground">
                    Extended statistics were not recorded for this result.
                  </p>
                )}
              </TabsContent>
            </Tabs>
          )}
        </>
      )}
    </>
  )
}
