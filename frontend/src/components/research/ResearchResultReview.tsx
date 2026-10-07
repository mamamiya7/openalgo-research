import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router'
import type { PortfolioJob, PortfolioResult } from '@/api/portfolioResearch'
import {
  type DecisionTarget,
  decisionOpeningTarget,
  type EvidenceOpeningIntent,
  researchDecisions,
} from '@/api/researchDecisions'
import { researchValidation, validationKey } from '@/api/researchValidation'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { researchError } from '@/hooks/useResearchExperiment'
import { researchReturnLocation } from '@/hooks/useResearchNavigation'
import { useAuthStore } from '@/stores/authStore'
import { CandidateDecision } from './CandidateDecision'
import { EvidenceOpened, EvidenceUse, FrozenDecisionReport } from './DecisionEvidence'
import { CompareMatchedBaseline } from './MatchedBaseline'
import { ChooseResearchSetup } from './ResearchChosenSetup'
import { researchDates } from './researchPresentation'

interface Props {
  experimentId: string
  job: PortfolioJob
  result: PortfolioResult
  readOnly: boolean
  decisionTarget?: DecisionTarget
}
export function ResearchResultReview(props: Props) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  return (
    <ResultReview
      key={JSON.stringify([
        owner,
        props.experimentId,
        props.job.id,
        props.result.report_context?.report_id,
      ])}
      {...props}
      owner={owner}
    />
  )
}
function ResultReview({
  owner,
  experimentId,
  job,
  result,
  readOnly,
  decisionTarget,
}: Props & { owner: string }) {
  const client = useQueryClient()
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const target = { job_id: job.id, config_id: result.report_context?.config_id ?? undefined }
  const expected = result.report_context?.result_artifact
    ? {
        result_artifact: result.report_context.result_artifact,
        analysis_artifact: result.report_context.analysis_artifact,
      }
    : undefined
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [embedded, setEmbedded] = useState<{ id: string; intent: EvidenceOpeningIntent } | null>(
    null
  )
  const controller = useRef<AbortController | null>(null)
  const request = useRef<{ signature: string; id: string } | null>(null)
  const context = useQuery({
    queryKey: [...validationKey(owner, experimentId), job.id, target.config_id],
    queryFn: ({ signal }) => researchValidation.context(experimentId, target, signal),
    gcTime: 0,
    staleTime: 30000,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const data = context.data
  const canonical = data
    ? { job_id: data.candidate.source_job_id, config_id: data.candidate.config_id }
    : target
  const preview = useQuery({
    queryKey: [...validationKey(owner, experimentId), 'embedded', embedded?.id],
    queryFn: ({ signal }) =>
      researchDecisions.preview(experimentId, canonical, embedded!.id, signal),
    enabled: Boolean(embedded),
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
  })
  useEffect(() => () => controller.current?.abort(), [])
  const state = location.state as {
    researchValidationOpen?: {
      jobId?: string
      requestId?: string
      experimentId?: string
      evidenceId?: string
    }
  } | null
  const opening = state?.researchValidationOpen
  const evidence = data?.evaluations.find(
    (item) =>
      item.job_id === job.id &&
      item.status === 'completed' &&
      item.evidence_id &&
      item.view === 'primary' &&
      result.report_context?.period === 'evaluation' &&
      item.identity?.result_artifact === expected?.result_artifact &&
      item.identity?.analysis_artifact === expected?.analysis_artifact &&
      item.identity?.config_id === target.config_id
  )
  const [observed, setObserved] = useState<{ id: string; intent: EvidenceOpeningIntent } | null>(
    null
  )
  useEffect(() => {
    if (
      opening?.jobId === job.id &&
      opening.experimentId === experimentId &&
      opening.requestId &&
      evidence?.evidence_id &&
      (!opening.evidenceId || opening.evidenceId === evidence.evidence_id)
    ) {
      const requestId = opening.requestId
      const id = evidence.evidence_id
      setObserved((old) =>
        old?.intent.request_id === requestId
          ? old
          : {
              id,
              intent: {
                request_id: requestId,
                target: decisionOpeningTarget(
                  { job_id: canonical.job_id, config_id: canonical.config_id },
                  id
                ),
              },
            }
      )
    }
  }, [
    opening?.jobId,
    opening?.experimentId,
    opening?.requestId,
    opening?.evidenceId,
    job.id,
    experimentId,
    evidence?.evidence_id,
    canonical.job_id,
    canonical.config_id,
  ])
  const intent =
    observed &&
    observed.id === evidence?.evidence_id &&
    observed.intent.request_id === opening?.requestId
      ? observed.intent
      : null
  function returnLocation() {
    return researchReturnLocation(params, experimentId)
  }
  function navigate(jobId: string, evidenceId?: string) {
    const next = new URLSearchParams({
      experiment: experimentId,
      view: 'backtests',
      job: jobId,
      return_research: returnLocation(),
    })
    setParams(next, {
      state: {
        researchValidationOpen: { jobId, experimentId, requestId: crypto.randomUUID(), evidenceId },
      },
    })
  }
  function history(id: string, eventId: string) {
    setParams({
      experiment: experimentId,
      view: 'decisions',
      decision: id,
      decision_event: eventId,
      return_research: returnLocation(),
    })
  }
  function close(value: boolean) {
    controller.current?.abort()
    controller.current = null
    setBusy(false)
    setOpen(value)
    setEmbedded(null)
    setError(null)
  }
  async function proceed() {
    if (!data || context.isFetching || context.isError || busy || controller.current) return
    if (
      data.action.kind === 'open' &&
      data.action.view === 'embedded_later' &&
      data.action.evidence_id
    ) {
      setEmbedded({
        id: data.action.evidence_id,
        intent: {
          request_id: crypto.randomUUID(),
          target: decisionOpeningTarget(canonical, data.action.evidence_id),
        },
      })
      return
    }
    if (['open', 'progress'].includes(data.action.kind) && data.action.job_id) {
      navigate(data.action.job_id, data.action.evidence_id)
      return
    }
    if (data.action.kind !== 'prepare' || readOnly || data.archived) return
    const payload = {
      revision: data.revision,
      source_result_artifact: data.candidate.source_result_artifact,
    }
    const signature = JSON.stringify([target, canonical, payload])
    if (request.current?.signature !== signature)
      request.current = { signature, id: crypto.randomUUID() }
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    setError(null)
    try {
      const receipt = await researchValidation.prepare(
        experimentId,
        target,
        { ...payload, request_id: request.current.id },
        abort.signal
      )
      if (abort.signal.aborted) return
      void client.invalidateQueries({ queryKey: ['research-experiment', owner, experimentId] })
      void client.invalidateQueries({ queryKey: validationKey(owner, experimentId) })
      navigate(receipt.job.id)
    } catch (cause) {
      if (!abort.signal.aborted) setError(researchError(cause))
    } finally {
      if (!abort.signal.aborted) {
        controller.current = null
        setBusy(false)
      }
    }
  }
  const label =
    data?.action.kind === 'open'
      ? 'Review later test'
      : data?.action.kind === 'progress'
        ? 'View later test'
        : 'Test later period'
  const later = result.report_context?.period === 'evaluation'
  return (
    <>
      <section aria-label="Research review" className="flex flex-wrap items-center gap-2">
        <CompareMatchedBaseline
          owner={owner}
          experimentId={experimentId}
          job={job}
          result={result}
          readOnly={readOnly}
        />
        {(data?.reservation || context.isError) && !later && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setOpen(true)
              void context.refetch()
            }}
          >
            {context.isError ? 'Review validation' : label}
          </Button>
        )}
        {later && data?.candidate.report_job_id && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const studyReport =
                data.candidate.report_job_id === data.candidate.source_job_id &&
                data.candidate.trial_number != null
              setParams({
                experiment: experimentId,
                view: studyReport ? 'studies' : 'backtests',
                job: data.candidate.report_job_id!,
                ...(studyReport ? { report: 'best' } : {}),
                return_research: returnLocation(),
              })
            }}
          >
            Selection report
          </Button>
        )}
        <CandidateDecision
          experimentId={experimentId}
          target={decisionTarget ?? { ...target, expected_report: expected }}
          name={
            result.report_context?.candidate?.trial_number != null
              ? `Trial ${result.report_context.candidate.trial_number + 1}`
              : data?.candidate.trial_number != null
                ? `Trial ${data.candidate.trial_number + 1}`
                : (result.portfolio?.name ?? job.specification?.portfolio?.name ?? 'This result')
          }
          readOnly={readOnly}
          currentDecision={data?.current}
          preferredEvaluationId={later ? evidence?.evidence_id : undefined}
          onHistory={history}
          onSaved={() => {
            void client.invalidateQueries({ queryKey: validationKey(owner, experimentId) })
          }}
        />
        {data?.current?.current.state === 'keep' && (
          <ChooseResearchSetup
            experimentId={experimentId}
            event={data.current.current}
            readOnly={readOnly}
          />
        )}
      </section>
      <EvidenceOpened owner={owner} experimentId={experimentId} intent={intent} />
      <Dialog open={open} onOpenChange={close}>
        <DialogContent
          className={
            embedded
              ? 'max-h-[92dvh] overflow-y-auto sm:max-w-6xl'
              : 'max-h-[90dvh] overflow-y-auto sm:max-w-lg'
          }
        >
          <DialogHeader>
            <DialogTitle>
              {embedded ? 'Later-period report' : 'Test the selected setup'}
            </DialogTitle>
            <DialogDescription>
              {embedded
                ? 'Saved results for the same candidate.'
                : 'Use the exact rules on the reserved dates.'}
            </DialogDescription>
          </DialogHeader>
          {embedded ? (
            <div className="space-y-4">
              <Button variant="ghost" size="sm" onClick={() => setEmbedded(null)}>
                Back to validation
              </Button>
              {preview.isPending && (
                <p className="text-sm text-muted-foreground">Opening saved report…</p>
              )}
              {preview.data?.available && preview.data.job && preview.data.result ? (
                <FrozenDecisionReport
                  owner={owner}
                  experimentId={experimentId}
                  identity={embedded.id}
                  name="Later-period test"
                  job={preview.data.job}
                  result={preview.data.result}
                  intent={embedded.intent}
                />
              ) : preview.isError || preview.data ? (
                <p role="alert" className="text-sm">
                  This saved report could not be opened.{' '}
                  <Button variant="link" size="sm" onClick={() => void preview.refetch()}>
                    Retry
                  </Button>
                </p>
              ) : null}
            </div>
          ) : (
            <div className="space-y-5">
              {context.isPending && (
                <p className="text-sm text-muted-foreground">Checking saved evidence…</p>
              )}
              {context.isError && (
                <p role="alert" className="text-sm">
                  Validation could not be loaded.{' '}
                  <Button variant="link" size="sm" onClick={() => void context.refetch()}>
                    Retry
                  </Button>
                </p>
              )}
              {data && (
                <>
                  <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-3 text-sm">
                    {data.candidate.trial_number != null && (
                      <>
                        <dt className="text-muted-foreground">Candidate</dt>
                        <dd>Trial {data.candidate.trial_number + 1}</dd>
                      </>
                    )}
                    <dt className="text-muted-foreground">Selection</dt>
                    <dd>
                      {researchDates(data.selection.from, data.selection.to) || 'Dates unrecorded'}
                    </dd>
                    <dt className="text-muted-foreground">Later test</dt>
                    <dd>
                      {data.reservation
                        ? researchDates(data.reservation.from, data.reservation.to)
                        : 'No reserved period'}
                    </dd>
                  </dl>
                  <p className="text-xs text-muted-foreground">
                    Saved prices and rules. Fresh starting cash; no positions carried over.
                  </p>
                  <EvidenceUse evidence={data.evidence_use} />
                  {data.action.kind === 'unavailable' && (
                    <p className="text-sm text-muted-foreground">
                      {data.action.reason ?? 'This result has no reserved period to test.'}
                    </p>
                  )}
                  {error && (
                    <p role="alert" className="text-sm text-destructive">
                      {error}{' '}
                      <Button
                        size="sm"
                        variant="link"
                        onClick={() => {
                          request.current = null
                          setError(null)
                          void context.refetch()
                        }}
                      >
                        Reload review
                      </Button>
                    </p>
                  )}
                  {data.action.kind !== 'unavailable' && (
                    <div className="flex justify-end">
                      <Button
                        disabled={
                          busy ||
                          context.isFetching ||
                          context.isError ||
                          (data.action.kind === 'prepare' && (readOnly || data.archived))
                        }
                        onClick={() => void proceed()}
                      >
                        {busy
                          ? 'Starting…'
                          : data.action.kind === 'prepare'
                            ? 'Run later test'
                            : data.action.kind === 'open'
                              ? 'Open saved report'
                              : 'Open progress'}
                      </Button>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
