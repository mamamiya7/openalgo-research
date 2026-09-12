import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import {
  type ComparisonMember,
  type ComparisonMetric,
  comparisonConflict,
  comparisonError,
  comparisonKey,
  researchComparisons,
  type SavedComparison,
} from '@/api/researchComparisons'
import type { EvidenceOpeningIntent } from '@/api/researchDecisions'
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
import { Textarea } from '@/components/ui/textarea'
import { useAuthStore } from '@/stores/authStore'
import { AnalysisFigure } from './AnalysisCharts'
import { CandidateDecision } from './CandidateDecision'
import { EvidenceOpened } from './DecisionEvidence'
import { PortfolioResults } from './PortfolioResults'
import { ReportCurrency, reportMoney } from './ReportCurrency'
import { ResearchResultReview } from './ResearchResultReview'
import { evaluationDifferenceLabels as differenceLabels } from './researchPresentation'

const number = (value: number) => value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
// Layout changes belong to the view; keep saved traces and their coordinates intact.
export function comparisonChartView(
  chart: SavedComparison['cumulative']
): SavedComparison['cumulative'] {
  if (!chart.figure) return chart
  return {
    ...chart,
    figure: {
      ...chart.figure,
      layout: {
        ...chart.figure.layout,
        title: undefined,
        margin: { l: 48, r: 16, t: 12, b: 120 },
        legend: {
          ...(chart.figure.layout.legend as object),
          orientation: 'h',
          x: 0,
          xanchor: 'left',
          y: -0.25,
          yanchor: 'top',
        },
      },
    },
  }
}
export function comparisonMetricText(
  metric: ComparisonMetric,
  value: unknown,
  currency: string | null,
  delta = false
) {
  if (metric.format === 'money' && !currency) return '—'
  if (typeof value === 'string' && !delta) return value || '—'
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  if (metric.format === 'money')
    return currency ? `${delta && value > 0 ? '+' : ''}${reportMoney(value, currency)}` : '—'
  return `${delta && value > 0 ? '+' : ''}${number(value)}${metric.format === 'percent' ? (delta ? ' pp' : '%') : ''}`
}
function memberCurrency(member: ComparisonMember): string | null {
  const basis = member.report_context.evaluation_basis
  return basis?.status === 'verified' &&
    typeof basis.comparison.currency === 'string' &&
    basis.comparison.currency
    ? basis.comparison.currency
    : null
}
export function ResearchComparisons(props: { experimentId: string; readOnly: boolean }) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  return <Comparisons key={`${owner}:${props.experimentId}`} {...props} owner={owner} />
}
function Comparisons({
  experimentId,
  readOnly,
  owner,
}: {
  experimentId: string
  readOnly: boolean
  owner: string
}) {
  const [params, setParams] = useSearchParams()
  const id = params.get('comparison')
  const memberId = params.get('comparison_member')
  const [opening, setOpening] = useState<EvidenceOpeningIntent | null>(null)
  useEffect(() => {
    if (!memberId) setOpening(null)
  }, [memberId])
  const rawOffset = Number(params.get('comparison_offset') ?? 0)
  const offset =
    Number.isSafeInteger(rawOffset) && rawOffset >= 0 && rawOffset <= 100000 ? rawOffset : 0
  const list = useQuery({
    queryKey: [...comparisonKey(owner, experimentId), 'page', offset, readOnly],
    queryFn: ({ signal }) => researchComparisons.list(experimentId, offset, signal),
    enabled: !id,
    gcTime: 0,
    retry: false,
  })
  function navigate(comparison: string | null, member: string | null = null) {
    const next = new URLSearchParams(params)
    if (comparison) next.set('comparison', comparison)
    else next.delete('comparison')
    if (member) next.set('comparison_member', member)
    else next.delete('comparison_member')
    setParams(next)
  }
  function page(value: number) {
    const next = new URLSearchParams(params)
    next.set('comparison_offset', String(value))
    setParams(next)
  }
  const backShortlist = () => {
    const next = new URLSearchParams(params)
    next.set('view', 'shortlist')
    next.delete('comparison')
    next.delete('comparison_member')
    setParams(next)
  }
  if (id && memberId)
    return (
      <FrozenMember
        key={`${owner}:${experimentId}:${id}:${memberId}`}
        owner={owner}
        experimentId={experimentId}
        id={id}
        memberId={memberId}
        readOnly={readOnly}
        intent={
          opening?.target.kind === 'comparison_member' &&
          opening.target.comparison_id === id &&
          opening.target.member_id === memberId
            ? opening
            : null
        }
        onBack={() => navigate(id)}
      />
    )
  if (id)
    return (
      <ComparisonDetail
        key={`${owner}:${experimentId}:${id}`}
        owner={owner}
        experimentId={experimentId}
        id={id}
        readOnly={readOnly}
        onBack={() => navigate(null)}
        onShortlist={backShortlist}
        onOpenMember={(member) => {
          setOpening({
            request_id: crypto.randomUUID(),
            target: { kind: 'comparison_member', comparison_id: id, member_id: member },
          })
          navigate(id, member)
        }}
        onDecisionHistory={(decision, eventId) => {
          const next = new URLSearchParams(params)
          next.set('view', 'decisions')
          next.set('decision', decision)
          next.set('decision_event', eventId)
          next.delete('decision_report')
          next.delete('decision_history_offset')
          setParams(next)
        }}
        onBackDecision={
          params.get('return_decision') && params.get('return_decision_event')
            ? () => {
                const next = new URLSearchParams(params)
                next.set('view', 'decisions')
                next.set('decision', params.get('return_decision')!)
                next.set('decision_event', params.get('return_decision_event')!)
                next.delete('decision_report')
                next.delete('return_decision')
                next.delete('return_decision_event')
                setParams(next)
              }
            : undefined
        }
      />
    )
  return (
    <section className="space-y-5" aria-label="Saved comparisons">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Comparisons</h2>
        <Button variant="outline" size="sm" onClick={backShortlist}>
          Open shortlist
        </Button>
      </div>
      {list.isPending && <p className="py-8 text-sm text-muted-foreground">Loading comparisons…</p>}
      {list.isError && (
        <LoadError message="Comparisons could not be loaded." retry={() => void list.refetch()} />
      )}
      {list.data && !list.data.items.length && (
        <p className="py-12 text-center text-sm text-muted-foreground">
          {offset ? 'No more comparisons.' : 'Compare 2–4 saved reports from your shortlist.'}
        </p>
      )}
      {list.data && list.data.items.length > 0 && (
        <div className="divide-y rounded-lg border">
          {list.data.items.map((item) => (
            <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div className="min-w-0">
                <button
                  type="button"
                  className="break-words text-left text-sm font-medium underline-offset-4 hover:underline"
                  onClick={() => navigate(item.id)}
                >
                  {item.name}
                </button>
                <p className="mt-1 max-w-3xl truncate text-xs text-muted-foreground">
                  {item.member_names.join(' · ')}
                </p>
              </div>
              <span className="text-xs text-muted-foreground">
                {item.member_count} reports ·{' '}
                {item.compatible ? 'Same test conditions' : 'Inspection only'}
              </span>
            </div>
          ))}
        </div>
      )}
      {list.data && (offset > 0 || list.data.next_offset !== null) && (
        <nav aria-label="Comparison pages" className="flex items-center justify-end gap-3 text-xs">
          <span>
            {Math.min(offset + 1, list.data.total)}–
            {Math.min(offset + list.data.items.length, list.data.total)} of {list.data.total}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={!offset}
            onClick={() => page(Math.max(0, offset - 20))}
          >
            Previous
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={list.data.next_offset === null}
            onClick={() => page(list.data!.next_offset!)}
          >
            Next
          </Button>
        </nav>
      )}
    </section>
  )
}
function LoadError({ message, retry }: { message: string; retry: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p role="alert" className="text-sm">
        {message}
      </p>
      <Button size="sm" variant="outline" onClick={retry}>
        Try again
      </Button>
    </div>
  )
}

function ComparisonDetail({
  owner,
  experimentId,
  id,
  readOnly,
  onBack,
  onShortlist,
  onOpenMember,
  onDecisionHistory,
  onBackDecision,
}: {
  owner: string
  experimentId: string
  id: string
  readOnly: boolean
  onBack: () => void
  onShortlist: () => void
  onOpenMember: (id: string) => void
  onDecisionHistory: (id: string, eventId: string) => void
  onBackDecision?: () => void
}) {
  const client = useQueryClient()
  const key = [...comparisonKey(owner, experimentId), 'detail', id, readOnly]
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => researchComparisons.get(experimentId, id, signal),
    gcTime: 0,
    retry: false,
  })
  const [edit, setEdit] = useState<{ revision: number; name: string; note: string } | null>(null)
  const editing = useRef(edit)
  editing.current = edit
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [search, setSearch] = useState('')
  const controller = useRef<AbortController | null>(null)
  useEffect(
    () => () => {
      controller.current?.abort()
      editing.current = null
    },
    []
  )
  const saved = query.data
  const archived = readOnly || Boolean(saved?.archived)
  async function update() {
    if (!edit || archived || controller.current) return
    const request = new AbortController()
    controller.current = request
    setBusy(true)
    setError(null)
    setConflict(false)
    try {
      const updated = await researchComparisons.update(experimentId, id, edit, request.signal)
      if (request.signal.aborted) return
      client.setQueryData(key, updated)
      void client.invalidateQueries({ queryKey: [...comparisonKey(owner, experimentId), 'page'] })
      setEdit(null)
    } catch (cause) {
      if (!request.signal.aborted) {
        setError(comparisonError(cause))
        setConflict(comparisonConflict(cause))
      }
    } finally {
      if (!request.signal.aborted) {
        controller.current = null
        setBusy(false)
      }
    }
  }
  return (
    <section className="space-y-6" aria-label="Saved comparison">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {onBackDecision && (
          <Button size="sm" variant="ghost" onClick={onBackDecision}>
            Back to decision
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onBack}>
          <ArrowLeft className="mr-2 size-4" />
          Back to comparisons
        </Button>
        <Button size="sm" variant="ghost" onClick={onShortlist}>
          Back to shortlist
        </Button>
      </div>
      {query.isPending && <p className="py-8 text-sm text-muted-foreground">Loading comparison…</p>}
      {query.isError && (
        <LoadError
          message="The saved comparison could not be loaded."
          retry={() => void query.refetch()}
        />
      )}
      {saved && (
        <>
          <header className="space-y-2">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <h2 className="min-w-0 break-words text-xl font-semibold">{saved.name}</h2>
              {!archived && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setEdit({ revision: saved.revision, name: saved.name, note: saved.note })
                    setError(null)
                    setConflict(false)
                  }}
                >
                  Edit name & note
                </Button>
              )}
            </div>
            {saved.note && (
              <p className="max-w-3xl whitespace-pre-wrap break-words text-sm text-muted-foreground">
                {saved.note}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {saved.members.length} saved reports · Reference:{' '}
              {saved.members.find((item) => item.id === saved.reference_member_id)?.name}
            </p>
          </header>
          {!saved.compatible && (
            <div className="space-y-2 rounded-lg border p-4 text-sm">
              <p className="font-medium">
                Inspection only — these reports use different or unverified inputs.
              </p>
              <ul className="space-y-1 text-xs text-muted-foreground">
                {saved.differences.map((item) => (
                  <li key={item.member_id}>
                    {saved.members.find((member) => member.id === item.member_id)?.name}:{' '}
                    {item.codes
                      .map((code) => differenceLabels[code] ?? code.replaceAll('_', ' '))
                      .join(', ')}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">
                Differences and a combined curve need matching test conditions.
              </p>
            </div>
          )}
          <MetricTable
            saved={saved}
            metrics={saved.metrics.filter((metric) => metric.source === 'summary')}
            onOpenMember={onOpenMember}
            decision={{ experimentId, readOnly: archived, onHistory: onDecisionHistory }}
          />
          {saved.compatible &&
            saved.cumulative.status === 'available' &&
            saved.cumulative.figure && (
              <section className="min-w-0 space-y-3" aria-label="Compared returns">
                <h3 className="text-sm font-medium">Cumulative return</h3>
                <AnalysisFigure chart={comparisonChartView(saved.cumulative)} height={380} />
              </section>
            )}
          {saved.compatible && saved.cumulative.status !== 'available' && (
            <p className="text-xs text-muted-foreground">
              {saved.cumulative.reason ?? 'A saved return curve is unavailable.'}
            </p>
          )}
          <details className="border-t pt-3">
            <summary className="cursor-pointer py-2 text-sm font-medium">Report context</summary>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              {saved.members.map((member) => (
                <div key={member.id} className="min-w-0 space-y-2 text-sm">
                  <h3 className="break-words font-medium">{member.name}</h3>
                  <p className="text-xs text-muted-foreground">
                    {member.report_context.period_label} ·{' '}
                    {member.report_context.dates.from ?? 'Unknown start'} –{' '}
                    {member.report_context.dates.to ?? 'Unknown end'}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {memberCurrency(member) ?? 'Currency unrecorded'} ·{' '}
                    {member.origin_kind === 'study'
                      ? `Trial ${(member.proposal_number ?? member.trial_number ?? 0) + 1}`
                      : 'Backtest'}
                  </p>
                  {member.note && (
                    <p className="whitespace-pre-wrap break-words text-xs">{member.note}</p>
                  )}
                </div>
              ))}
            </div>
          </details>
          {saved.metrics.some((metric) => metric.source === 'analysis') && (
            <details className="border-t pt-3">
              <summary className="cursor-pointer py-2 text-sm font-medium">More statistics</summary>
              <div className="mt-3 space-y-4">
                <Input
                  aria-label="Find comparison statistic"
                  className="max-w-sm"
                  placeholder="Find a statistic…"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
                <MetricTable
                  saved={saved}
                  metrics={saved.metrics.filter(
                    (metric) =>
                      metric.source === 'analysis' &&
                      `${metric.label} ${metric.description}`
                        .toLowerCase()
                        .includes(search.toLowerCase().trim())
                  )}
                />
              </div>
            </details>
          )}
        </>
      )}
      <Dialog
        open={edit !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setEdit(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit comparison</DialogTitle>
            <DialogDescription>The saved reports and reference stay fixed.</DialogDescription>
          </DialogHeader>
          {edit && (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault()
                void update()
              }}
            >
              <div className="space-y-2">
                <Label htmlFor="comparison-name">Name</Label>
                <Input
                  id="comparison-name"
                  maxLength={120}
                  value={edit.name}
                  onChange={(event) => setEdit({ ...edit, name: event.target.value })}
                  disabled={busy || archived}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="comparison-note">Note</Label>
                <Textarea
                  id="comparison-note"
                  maxLength={2000}
                  rows={5}
                  value={edit.note}
                  onChange={(event) => setEdit({ ...edit, note: event.target.value })}
                  disabled={busy || archived}
                />
              </div>
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              {conflict && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={query.isFetching}
                  onClick={() => {
                    void query.refetch().then((response) => {
                      if (response.data && editing.current === edit) {
                        setEdit({
                          revision: response.data.revision,
                          name: response.data.name,
                          note: response.data.note,
                        })
                        setConflict(false)
                        setError(null)
                      }
                    })
                  }}
                >
                  Reload saved details
                </Button>
              )}
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" disabled={busy} onClick={() => setEdit(null)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={busy || archived || conflict || !edit.name.trim()}>
                  {busy ? 'Saving…' : 'Save changes'}
                </Button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </section>
  )
}
function MetricTable({
  saved,
  metrics,
  onOpenMember,
  decision,
}: {
  saved: SavedComparison
  metrics: ComparisonMetric[]
  onOpenMember?: (id: string) => void
  decision?: {
    experimentId: string
    readOnly: boolean
    onHistory: (id: string, eventId: string) => void
  }
}) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <caption className="sr-only">
          Saved report statistics{saved.compatible ? ' and differences from the reference' : ''}
        </caption>
        <thead className="bg-muted/50">
          <tr>
            <th
              scope="col"
              className="sticky left-0 z-10 min-w-32 border-r bg-muted px-4 py-3 text-left text-xs font-medium"
            >
              Statistic
            </th>
            {saved.members.map((member) => (
              <th
                key={member.id}
                scope="col"
                className="min-w-44 max-w-64 px-4 py-3 text-left text-sm font-medium"
              >
                <span className="block break-words">{member.name}</span>
                {member.id === saved.reference_member_id && (
                  <span className="mt-1 block text-xs font-normal text-muted-foreground">
                    Reference
                  </span>
                )}
                {onOpenMember && (
                  <Button
                    variant="link"
                    size="sm"
                    className="mt-1 h-auto p-0"
                    disabled={!member.available}
                    onClick={() => onOpenMember(member.id)}
                    aria-label={`Open report for ${member.name}`}
                  >
                    Open report
                  </Button>
                )}
                {onOpenMember && !member.available && (
                  <p className="mt-1 text-xs font-normal text-muted-foreground">
                    {member.error ?? 'Saved report unavailable'}
                  </p>
                )}
                {decision && (
                  <div className="mt-1">
                    <CandidateDecision
                      experimentId={decision.experimentId}
                      target={{ comparison_id: saved.id, member_id: member.id }}
                      name={member.name}
                      readOnly={decision.readOnly}
                      onHistory={decision.onHistory}
                    />
                  </div>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {metrics.map((metric) => (
            <tr key={metric.key}>
              <th
                scope="row"
                className="sticky left-0 z-10 border-r bg-background px-4 py-3 text-left text-xs font-normal"
              >
                <details>
                  <summary className="cursor-pointer list-none underline-offset-4 hover:underline">
                    {metric.label}
                  </summary>
                  <p className="mt-2 max-w-64 text-xs leading-5 text-muted-foreground">
                    {metric.description}
                  </p>
                </details>
              </th>
              {saved.members.map((member) => {
                const value = comparisonMetricText(metric, metric.values[member.id], saved.currency)
                const delta =
                  saved.compatible && member.id !== saved.reference_member_id
                    ? metric.deltas?.[member.id]
                    : null
                return (
                  <td key={member.id} className="px-4 py-3 tabular-nums">
                    <span title={value === '—' ? metric.unavailable[member.id] : undefined}>
                      {value}
                    </span>
                    {typeof delta === 'number' && Number.isFinite(delta) && (
                      <output
                        className="mt-1 block text-xs text-muted-foreground"
                        aria-label={`${metric.label} difference for ${member.name}`}
                      >
                        {comparisonMetricText(metric, delta, saved.currency, true)}
                      </output>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
          {!metrics.length && (
            <tr>
              <td colSpan={saved.members.length + 1} className="p-6 text-muted-foreground">
                No saved statistics match.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
function FrozenMember({
  owner,
  experimentId,
  id,
  memberId,
  readOnly,
  onBack,
  intent,
}: {
  owner: string
  experimentId: string
  id: string
  memberId: string
  readOnly: boolean
  onBack: () => void
  intent: EvidenceOpeningIntent | null
}) {
  const report = useQuery({
    queryKey: [...comparisonKey(owner, experimentId), 'member', id, memberId, readOnly],
    queryFn: ({ signal }) => researchComparisons.member(experimentId, id, memberId, signal),
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
  })
  const data = report.data
  return (
    <section className="space-y-5" aria-label="Comparison member report">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="mr-2 size-4" />
        Back to comparison
      </Button>
      {report.isPending && (
        <p className="py-8 text-sm text-muted-foreground">Opening saved report…</p>
      )}
      {report.isError && (
        <LoadError
          message="The saved report could not be opened."
          retry={() => void report.refetch()}
        />
      )}
      {data && (!data.available || !data.job || !data.result) && (
        <output className="block text-sm text-muted-foreground">
          {data.error ?? 'The original saved report is unavailable.'}
        </output>
      )}
      {data?.available && data.job && data.result && (
        <>
          <p className="text-xs text-muted-foreground">
            {data.member.name} · Saved comparison report
            {!memberCurrency(data.member) ? ' · Currency unrecorded' : ''}
          </p>
          <ResearchResultReview
            experimentId={experimentId}
            job={data.job}
            result={data.result}
            readOnly={readOnly}
            decisionTarget={{ comparison_id: id, member_id: memberId }}
          />
          <ReportCurrency.Provider value={memberCurrency(data.member)}>
            <PortfolioResults
              key={JSON.stringify([
                id,
                memberId,
                data.member.report_result_artifact,
                data.member.analysis_artifact,
              ])}
              job={data.job}
              result={data.result}
              readOnly
              freezeAnalysis
              embedded
              exportUrl=""
              onRerun={() => undefined}
              rerunning={false}
            />
          </ReportCurrency.Provider>
          <EvidenceOpened owner={owner} experimentId={experimentId} intent={intent} />
        </>
      )}
    </section>
  )
}
