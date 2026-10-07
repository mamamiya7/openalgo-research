import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import type { PortfolioJob, PortfolioResult } from '@/api/portfolioResearch'
import { baselineMatchKey, researchBaselines } from '@/api/researchBaselines'
import { comparisonKey, researchComparisons } from '@/api/researchComparisons'
import { researchShortlist, type ShortlistCandidate, shortlistKey } from '@/api/researchShortlist'
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
import { evaluationDifferenceLabels, researchDates } from './researchPresentation'

function returnLocation(params: URLSearchParams, experimentId: string) {
  const back = new URLSearchParams(params)
  back.delete('matched_study_candidate')
  back.delete('matched_compare_request')
  return researchReturnLocation(back, experimentId)
}
export function MatchBaseline({
  owner,
  experimentId,
  study,
  baseline,
  readOnly,
}: {
  owner: string
  experimentId: string
  study: ShortlistCandidate
  baseline: ShortlistCandidate
  readOnly: boolean
}) {
  const [params, setParams] = useSearchParams()
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const active = useRef<AbortController | null>(null)
  const request = useRef<{ signature: string; id: string } | null>(null)
  const context = useQuery({
    queryKey: [
      ...baselineMatchKey(owner, experimentId),
      study.source_job_id,
      study.source_result_artifact,
      baseline.source_job_id,
      baseline.source_result_artifact,
    ],
    queryFn: ({ signal }) =>
      researchBaselines.context(experimentId, study.source_job_id, baseline.source_job_id, signal),
    enabled: open,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  })
  useEffect(() => () => active.current?.abort(), [])
  useEffect(() => {
    if (readOnly) {
      active.current?.abort()
      active.current = null
      setBusy(false)
    }
  }, [readOnly])
  const stale = Boolean(
    context.data &&
      (context.data.study.result_artifact !== study.source_result_artifact ||
        context.data.baseline.result_artifact !== baseline.source_result_artifact)
  )
  function close(value: boolean) {
    active.current?.abort()
    active.current = null
    setBusy(false)
    setOpen(value)
    setError(null)
    if (!value)
      void client.cancelQueries({
        queryKey: [...baselineMatchKey(owner, experimentId), study.source_job_id],
      })
  }
  function navigate(jobId: string) {
    setParams({
      experiment: experimentId,
      view: 'backtests',
      job: jobId,
      matched_study_candidate: study.id,
      matched_compare_request: crypto.randomUUID(),
      return_research: returnLocation(params, experimentId),
    })
  }
  async function proceed() {
    const data = context.data
    if (
      !data ||
      context.isFetching ||
      context.isError ||
      stale ||
      active.current ||
      readOnly ||
      data.archived
    )
      return
    if (data.action.job_id && ['open', 'progress'].includes(data.action.kind)) {
      navigate(data.action.job_id)
      return
    }
    if (data.action.kind !== 'prepare') return
    const payload = {
      revision: data.revision,
      study_result_artifact: study.source_result_artifact,
      baseline_result_artifact: baseline.source_result_artifact,
    }
    const signature = JSON.stringify(payload)
    if (request.current?.signature !== signature)
      request.current = { signature, id: crypto.randomUUID() }
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      const saved = await researchBaselines.prepare(
        experimentId,
        study.source_job_id,
        baseline.source_job_id,
        { ...payload, request_id: request.current.id },
        controller.signal
      )
      if (controller.signal.aborted) return
      void client.invalidateQueries({ queryKey: ['research-experiment', owner, experimentId] })
      navigate(saved.job.id)
    } catch (cause) {
      if (!controller.signal.aborted) setError(researchError(cause))
    } finally {
      if (!controller.signal.aborted) {
        active.current = null
        setBusy(false)
      }
    }
  }
  return (
    <>
      <Button variant="outline" size="sm" disabled={readOnly} onClick={() => close(true)}>
        Match baseline
      </Button>
      <Dialog open={open} onOpenChange={close}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Match the baseline to this study</DialogTitle>
            <DialogDescription>
              Compare the original rules on the same saved data.
            </DialogDescription>
          </DialogHeader>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-sm">
            <dt className="text-muted-foreground">Baseline</dt>
            <dd className="break-words">{baseline.name}</dd>
            <dt className="text-muted-foreground">Study result</dt>
            <dd className="break-words">{study.name}</dd>
            {context.data?.recipe && (
              <>
                <dt className="text-muted-foreground">Use study data</dt>
                <dd>
                  {researchDates(context.data.dates.from, context.data.dates.to)}
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {context.data.recipe.eligible_signals.toLocaleString('en-IN')} eligible signals
                  </span>
                </dd>
              </>
            )}
          </dl>
          {context.data?.differences.length ? (
            <p className="text-xs text-muted-foreground">
              Align:{' '}
              {context.data.differences
                .map((code) => evaluationDifferenceLabels[code] ?? 'Saved evaluation data')
                .join(' · ')}
            </p>
          ) : null}
          {stale && (
            <p role="alert" className="text-sm">
              The saved study or baseline changed. Reopen them before matching.
            </p>
          )}
          {context.isPending && (
            <p className="text-sm text-muted-foreground">Checking the saved rules and data…</p>
          )}
          {context.isError && (
            <p role="alert" className="text-sm">
              {researchError(context.error)}{' '}
              <Button variant="link" size="sm" onClick={() => void context.refetch()}>
                Retry
              </Button>
            </p>
          )}
          {context.data?.action.kind === 'unavailable' ? (
            <p className="text-sm text-muted-foreground">{context.data.action.reason}</p>
          ) : (
            context.data?.recipe && (
              <p className="text-xs text-muted-foreground">
                Original trading rules and capital. A new result; the original baseline stays saved.
              </p>
            )
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}{' '}
              <Button
                variant="link"
                size="sm"
                onClick={() => {
                  setError(null)
                  request.current = null
                  void context.refetch()
                }}
              >
                Reload review
              </Button>
            </p>
          )}
          {context.data && context.data.action.kind !== 'unavailable' && (
            <div className="flex justify-end">
              <Button
                disabled={
                  busy ||
                  context.isFetching ||
                  context.isError ||
                  stale ||
                  readOnly ||
                  context.data.archived
                }
                onClick={() => void proceed()}
              >
                {busy
                  ? 'Starting…'
                  : context.data.action.kind === 'prepare'
                    ? 'Create matching baseline'
                    : context.data.action.kind === 'open'
                      ? 'Open matching baseline'
                      : 'Open progress'}
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

export function CompareMatchedBaseline({
  owner,
  experimentId,
  job,
  result,
  readOnly,
}: {
  owner: string
  experimentId: string
  job: PortfolioJob
  result: PortfolioResult
  readOnly: boolean
}) {
  const [params, setParams] = useSearchParams()
  const candidateId = params.get('matched_study_candidate')
  const origin = result.matched_baseline_origin
  const viewed = result.report_context
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const active = useRef<AbortController | null>(null)
  const request = useRef<{ signature: string; id: string } | null>(null)
  useEffect(() => () => active.current?.abort(), [])
  useEffect(() => {
    if (readOnly) {
      active.current?.abort()
      active.current = null
      setBusy(false)
    }
  }, [readOnly])
  async function compare() {
    if (
      !origin ||
      !viewed?.result_artifact ||
      !viewed.config_id ||
      !candidateId ||
      active.current ||
      readOnly
    )
      return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      const study = await researchShortlist.get(experimentId, candidateId, controller.signal)
      if (controller.signal.aborted) return
      if (
        study.candidate.source_job_id !== origin.study_job_id ||
        study.candidate.source_result_artifact !== origin.study_result_artifact ||
        !study.available ||
        study.archived
      )
        throw new Error('The study candidate changed. Return to your shortlist to choose it again.')
      const baseline = await researchShortlist.save(
        experimentId,
        {
          job_id: job.id,
          config_id: viewed.config_id,
          expected_result_artifact: viewed.result_artifact,
        },
        controller.signal
      )
      if (controller.signal.aborted) return
      if (
        baseline.candidate.source_result_artifact !== viewed.result_artifact ||
        baseline.candidate.config_id !== viewed.config_id ||
        baseline.candidate.source_job_id !== job.id ||
        baseline.candidate.period !== viewed.period
      )
        throw new Error('This matching baseline changed. Reload its report before comparing.')
      const payload = {
        candidate_ids: [candidateId, baseline.candidate.id],
        reference_candidate_id: baseline.candidate.id,
      }
      const signature = JSON.stringify(payload)
      if (request.current?.signature !== signature)
        request.current = {
          signature,
          id: params.get('matched_compare_request') || crypto.randomUUID(),
        }
      if (!params.has('matched_compare_request')) {
        const next = new URLSearchParams(params)
        next.set('matched_compare_request', request.current.id)
        setParams(next, { replace: true })
      }
      const saved = await researchComparisons.save(
        experimentId,
        { ...payload, request_id: request.current.id },
        controller.signal
      )
      if (controller.signal.aborted) return
      void client.invalidateQueries({ queryKey: shortlistKey(owner, experimentId) })
      void client.invalidateQueries({ queryKey: comparisonKey(owner, experimentId) })
      setParams({ experiment: experimentId, view: 'comparisons', comparison: saved.comparison.id })
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(
          cause instanceof Error &&
            (cause.message.startsWith('The study candidate') ||
              cause.message.startsWith('This matching baseline'))
            ? cause.message
            : researchError(cause)
        )
    } finally {
      if (!controller.signal.aborted) {
        active.current = null
        setBusy(false)
      }
    }
  }
  if (
    !candidateId ||
    !origin ||
    !viewed?.result_artifact ||
    !viewed.config_id ||
    origin.verification?.basis !== 'matched' ||
    origin.verification?.settings !== 'matched' ||
    readOnly
  )
    return null
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" disabled={busy} onClick={() => void compare()}>
        {busy ? 'Saving comparison…' : 'Compare with study'}
      </Button>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
