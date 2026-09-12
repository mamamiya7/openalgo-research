import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import {
  type DecisionState,
  decisionKey,
  type EvidenceOpeningIntent,
  researchDecisions,
} from '@/api/researchDecisions'
import { Button } from '@/components/ui/button'
import { researchBackParams, researchNavigationReturn } from '@/hooks/useResearchNavigation'
import { useAuthStore } from '@/stores/authStore'
import { CandidateDecision } from './CandidateDecision'
import { decisionDate, decisionLabels, EvidenceUse, FrozenDecisionReport } from './DecisionEvidence'
import { ChooseResearchSetup } from './ResearchChosenSetup'

const pageOffset = (value: string | null) => {
  const parsed = Number(value ?? 0)
  return Number.isSafeInteger(parsed) && parsed >= 0 && parsed <= 100000 ? parsed : 0
}
export function ResearchDecisions(props: { experimentId: string; readOnly: boolean }) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  return <Decisions key={`${owner}:${props.experimentId}`} {...props} owner={owner} />
}
function Decisions({
  owner,
  experimentId,
  readOnly,
}: {
  owner: string
  experimentId: string
  readOnly: boolean
}) {
  const [params, setParams] = useSearchParams()
  const [intent, setIntent] = useState<EvidenceOpeningIntent | null>(null)
  const id = params.get('decision')
  const eventId = params.get('decision_event')
  const evidence = params.get('decision_report')
  const reportEvidence = evidence === 'selection' || evidence === 'evaluation' ? evidence : null
  const returnResearch = researchBackParams(params.get('return_research'), experimentId)
  useEffect(() => {
    if (!reportEvidence) setIntent(null)
  }, [reportEvidence])
  const stateValue = params.get('decision_state')
  const state: DecisionState | '' =
    stateValue === 'keep' || stateValue === 'reject' || stateValue === 'revisit' ? stateValue : ''
  const offset = pageOffset(params.get('decision_offset'))
  const historyOffset = pageOffset(params.get('decision_history_offset'))
  const list = useQuery({
    queryKey: [...decisionKey(owner, experimentId), 'page', state, offset, readOnly],
    queryFn: ({ signal }) => researchDecisions.list(experimentId, state, offset, signal),
    enabled: !id,
    gcTime: 0,
    retry: false,
  })
  const history = useQuery({
    queryKey: [...decisionKey(owner, experimentId), 'history', id, historyOffset, readOnly],
    queryFn: ({ signal }) => researchDecisions.history(experimentId, id!, historyOffset, signal),
    enabled: Boolean(id && !reportEvidence),
    gcTime: 0,
    retry: false,
  })
  const event = useQuery({
    queryKey: [...decisionKey(owner, experimentId), 'event', id, eventId, readOnly],
    queryFn: ({ signal }) => researchDecisions.event(experimentId, id!, eventId!, signal),
    enabled: Boolean(id && eventId),
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
  })
  const report = useQuery({
    queryKey: [...decisionKey(owner, experimentId), 'report', id, eventId, reportEvidence],
    queryFn: ({ signal }) =>
      researchDecisions.report(experimentId, id!, eventId!, reportEvidence!, signal),
    enabled: Boolean(id && eventId && reportEvidence),
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
  })
  function navigate(values: Record<string, string | null>) {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(values)) {
      if (value === null) next.delete(key)
      else next.set(key, value)
    }
    setParams(next)
  }
  function select(decision: string, selected: string) {
    setIntent(null)
    navigate({ decision, decision_event: selected, decision_report: null })
  }
  function openReport(which: 'selection' | 'evaluation') {
    if (!id || !eventId) return
    setIntent({
      request_id: crypto.randomUUID(),
      target: { kind: 'decision_event', decision_id: id, event_id: eventId, evidence: which },
    })
    navigate({ decision_report: which })
  }
  const saved = event.data?.event
  const savedTarget =
    saved?.target ??
    (saved?.comparison_id && saved.member_id
      ? { comparison_id: saved.comparison_id, member_id: saved.member_id }
      : null)
  const matchingIntent =
    intent?.target.kind === 'decision_event' &&
    intent.target.decision_id === id &&
    intent.target.event_id === eventId &&
    intent.target.evidence === reportEvidence
      ? intent
      : null
  if (id && eventId && reportEvidence)
    return (
      <section className="space-y-5" aria-label="Decision report">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setIntent(null)
            navigate({ decision_report: null })
          }}
        >
          Back to decision
        </Button>
        {report.isPending && (
          <p className="py-8 text-sm text-muted-foreground">Opening saved report…</p>
        )}
        {report.isError && (
          <ReadError
            message="The saved report could not be opened."
            retry={() => void report.refetch()}
          />
        )}
        {report.data && (!report.data.available || !report.data.job || !report.data.result) && (
          <p className="text-sm text-muted-foreground">
            {report.data.error ?? 'The saved report is unavailable.'}
          </p>
        )}
        {report.data?.available && report.data.job && report.data.result && (
          <FrozenDecisionReport
            key={`${id}:${eventId}:${reportEvidence}`}
            owner={owner}
            experimentId={experimentId}
            identity={`${id}:${eventId}:${reportEvidence}`}
            name={saved?.candidate_name ?? 'Decision evidence'}
            job={report.data.job}
            result={report.data.result}
            intent={matchingIntent}
          />
        )}
      </section>
    )
  if (id)
    return (
      <section className="space-y-5" aria-label="Decision history">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              returnResearch
                ? setParams(returnResearch, {
                    state: researchNavigationReturn(owner, experimentId, returnResearch),
                  })
                : navigate({
                    decision: null,
                    decision_event: null,
                    decision_report: null,
                    decision_history_offset: null,
                  })
            }
          >
            {returnResearch ? 'Back to research' : 'Back to decisions'}
          </Button>
          {saved?.comparison_id && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                navigate({
                  view: 'comparisons',
                  comparison: saved.comparison_id,
                  comparison_member: null,
                  return_decision: id,
                  return_decision_event: saved.id,
                })
              }
            >
              Open comparison
            </Button>
          )}
        </div>
        {eventId && event.isPending && (
          <p className="py-8 text-sm text-muted-foreground">Loading decision…</p>
        )}
        {event.isError && (
          <ReadError
            message="The decision could not be loaded."
            retry={() => void event.refetch()}
          />
        )}
        {saved && (
          <article className="space-y-4">
            <header className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <h2 className="break-words text-xl font-semibold">{saved.candidate_name}</h2>
                <p className="text-xs text-muted-foreground">
                  {saved.comparison_name ?? 'Saved report'}
                </p>
              </div>
              {savedTarget && (
                <CandidateDecision
                  experimentId={experimentId}
                  target={savedTarget}
                  name={saved.candidate_name}
                  readOnly={readOnly || Boolean(event.data?.archived)}
                  onHistory={select}
                  onSaved={select}
                />
              )}
            </header>
            <div className="space-y-2">
              <p className="font-semibold">
                {decisionLabels[saved.state]}{' '}
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  {decisionDate(saved.created_at)} · Revision {saved.revision}
                </span>
              </p>
              {saved.reason && (
                <p className="max-w-3xl whitespace-pre-wrap break-words text-sm">{saved.reason}</p>
              )}
            </div>
            {saved.evaluation && (
              <p className="text-xs text-muted-foreground">
                Later results used for this decision · {saved.evaluation.dates.from} –{' '}
                {saved.evaluation.dates.to}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <ChooseResearchSetup
                experimentId={experimentId}
                event={saved}
                readOnly={readOnly || Boolean(event.data?.archived) || !event.data?.available}
              />
              <Button size="sm" variant="outline" onClick={() => openReport('selection')}>
                Open report
              </Button>
              {saved.evaluation && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!saved.evaluation.available}
                  onClick={() => openReport('evaluation')}
                >
                  Open later report
                </Button>
              )}
            </div>
            {!event.data?.available && (
              <p className="text-sm text-muted-foreground">
                {event.data?.error ?? 'The saved report is unavailable.'}
              </p>
            )}
            <EvidenceUse evidence={saved.evidence_use} />
          </article>
        )}
        <div className="space-y-3 border-t pt-5">
          <h3 className="text-sm font-semibold">History</h3>
          {history.isPending && <p className="text-sm text-muted-foreground">Loading history…</p>}
          {history.isError && (
            <ReadError
              message="Decision history could not be loaded."
              retry={() => void history.refetch()}
            />
          )}
          {history.data?.items.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={item.id === eventId ? 'true' : undefined}
              aria-label={`Open revision ${item.revision}`}
              className="block w-full rounded-md border px-4 py-3 text-left hover:bg-muted/50 aria-[current=true]:bg-muted"
              onClick={() => select(id, item.id)}
            >
              <span className="flex flex-wrap justify-between gap-2 text-sm">
                <span className="font-medium">{decisionLabels[item.state]}</span>
                <span className="text-xs text-muted-foreground">
                  Revision {item.revision} · {decisionDate(item.created_at)}
                </span>
              </span>
              {item.reason && (
                <span className="mt-1 line-clamp-2 block break-words text-xs text-muted-foreground">
                  {item.reason}
                </span>
              )}
            </button>
          ))}
          {history.data && (
            <Pages
              offset={historyOffset}
              next={history.data.next_offset}
              onPage={(value) => navigate({ decision_history_offset: String(value) })}
            />
          )}
        </div>
      </section>
    )
  return (
    <section className="space-y-5" aria-label="Saved decisions">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Decisions</h2>
        <select
          aria-label="Filter decisions"
          className="rounded-md border bg-background px-3 py-2 text-sm"
          value={state}
          onChange={(event) =>
            navigate({ decision_state: event.target.value || null, decision_offset: null })
          }
        >
          <option value="">All decisions</option>
          {Object.entries(decisionLabels).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </header>
      {list.isPending && <p className="py-8 text-sm text-muted-foreground">Loading decisions…</p>}
      {list.isError && (
        <ReadError message="Decisions could not be loaded." retry={() => void list.refetch()} />
      )}
      {list.data && !list.data.items.length && (
        <p className="py-12 text-center text-sm text-muted-foreground">
          {state
            ? 'No decisions match.'
            : 'Keep, reject or revisit a result from its report to save your research here.'}
        </p>
      )}
      {list.data && list.data.items.length > 0 && (
        <div className="divide-y rounded-lg border">
          {list.data.items.map((item) => (
            <button
              type="button"
              key={item.id}
              aria-label={`View decision for ${item.current.candidate_name}`}
              className="block w-full px-4 py-4 text-left hover:bg-muted/50"
              onClick={() => select(item.id, item.current.id)}
            >
              <span className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 break-words text-sm font-medium">
                  {item.current.candidate_name}
                </span>
                <span className="text-xs font-medium">{decisionLabels[item.current.state]}</span>
              </span>
              <span className="mt-1 block text-xs text-muted-foreground">
                {decisionDate(item.current.created_at)}
              </span>
              {item.current.reason && (
                <span className="mt-2 line-clamp-2 block break-words text-sm text-muted-foreground">
                  {item.current.reason}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
      {list.data && (
        <Pages
          offset={offset}
          next={list.data.next_offset}
          onPage={(value) => navigate({ decision_offset: String(value) })}
        />
      )}
    </section>
  )
}
function ReadError({ message, retry }: { message: string; retry: () => void }) {
  return (
    <p role="alert" className="text-sm">
      {message}{' '}
      <Button variant="link" size="sm" onClick={retry}>
        Retry
      </Button>
    </p>
  )
}
function Pages({
  offset,
  next,
  onPage,
}: {
  offset: number
  next: number | null
  onPage: (value: number) => void
}) {
  return offset > 0 || next !== null ? (
    <div className="flex justify-end gap-2">
      <Button
        variant="ghost"
        size="sm"
        disabled={offset === 0}
        onClick={() => onPage(Math.max(0, offset - 20))}
      >
        Previous
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={next === null}
        onClick={() => next !== null && onPage(next)}
      >
        Next
      </Button>
    </div>
  ) : null
}
