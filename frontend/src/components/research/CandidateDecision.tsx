import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import {
  type DecisionContext,
  type DecisionState,
  type DecisionSummary,
  type DecisionTarget,
  decisionConflict,
  decisionError,
  decisionEvidenceChanged,
  decisionKey,
  decisionOpeningTarget,
  decisionTargetKey,
  type EvidenceOpeningIntent,
  researchDecisions,
} from '@/api/researchDecisions'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useAuthStore } from '@/stores/authStore'
import { decisionLabels, EvidenceUse, FrozenDecisionReport } from './DecisionEvidence'

interface Props {
  experimentId: string
  target: DecisionTarget
  name: string
  readOnly: boolean
  onHistory: (id: string, eventId: string) => void
  onSaved?: (id: string, eventId: string) => void
  currentDecision?: DecisionSummary | null
  preferredEvaluationId?: string
}
interface Draft {
  revision: number
  state: DecisionState | ''
  reason: string
  evaluation: string
}
const initial = (context: DecisionContext, preferredEvaluationId?: string): Draft => ({
  revision: context.current?.revision ?? 0,
  state: context.current?.current.state ?? '',
  reason: context.current?.current.reason ?? '',
  evaluation: context.current
    ? (context.current.current.evaluation?.id ?? '')
    : (context.evaluation.items.find((item) => item.id === preferredEvaluationId)?.id ?? ''),
})
export function CandidateDecision(props: Props) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  return (
    <DecisionAction
      key={JSON.stringify([owner, props.experimentId, decisionTargetKey(props.target)])}
      {...props}
      owner={owner}
    />
  )
}
function DecisionAction({
  owner,
  experimentId,
  target,
  name,
  readOnly,
  onHistory,
  onSaved,
  currentDecision,
  preferredEvaluationId,
}: Props & { owner: string }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [offset, setOffset] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [evidenceChanged, setEvidenceChanged] = useState(false)
  const [preview, setPreview] = useState<{ id: string; intent: EvidenceOpeningIntent } | null>(null)
  const controller = useRef<AbortController | null>(null)
  const request = useRef<{ signature: string; id: string } | null>(null)
  const opened = useRef(open)
  const dialogGeneration = useRef(0)
  opened.current = open
  const key = [
    ...decisionKey(owner, experimentId),
    'context',
    ...decisionTargetKey(target),
    offset,
    readOnly,
  ]
  const context = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => researchDecisions.context(experimentId, target, offset, signal),
    enabled: open && !preview,
    gcTime: 0,
    retry: false,
  })
  const previewQuery = useQuery({
    queryKey: [
      ...decisionKey(owner, experimentId),
      'preview',
      ...decisionTargetKey(target),
      preview?.id,
    ],
    queryFn: ({ signal }) => researchDecisions.preview(experimentId, target, preview!.id, signal),
    enabled: Boolean(open && preview),
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
  })
  useEffect(() => {
    if (open && !draft && context.data) setDraft(initial(context.data, preferredEvaluationId))
  }, [open, draft, context.data, preferredEvaluationId])
  useEffect(
    () => () => {
      controller.current?.abort()
      opened.current = false
    },
    []
  )
  const archived = readOnly || Boolean(context.data?.archived)
  const current = context.data?.current ?? currentDecision
  const receipts = context.data?.evaluation.items ?? []
  const previousEvaluation = current?.current.evaluation
  const missingSelected = Boolean(
    draft?.evaluation && !receipts.some((item) => item.id === draft.evaluation)
  )
  function close(value: boolean) {
    dialogGeneration.current += 1
    controller.current?.abort()
    controller.current = null
    setBusy(false)
    setOpen(value)
    setPreview(null)
    if (!value) {
      void client.cancelQueries({
        queryKey: [...decisionKey(owner, experimentId), 'context', ...decisionTargetKey(target)],
      })
      void client.cancelQueries({
        queryKey: [...decisionKey(owner, experimentId), 'preview', ...decisionTargetKey(target)],
      })
    }
    if (value) {
      setDraft(context.data ? initial(context.data, preferredEvaluationId) : null)
      setError(null)
      setConflict(false)
      setEvidenceChanged(false)
    }
  }
  async function save() {
    if (!draft?.state || archived || controller.current) return
    const data = {
      revision: draft.revision,
      state: draft.state,
      reason: draft.reason,
      evaluation_id: draft.evaluation || null,
    }
    const signature = JSON.stringify(data)
    if (request.current?.signature !== signature)
      request.current = { signature, id: crypto.randomUUID() }
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    setError(null)
    setConflict(false)
    setEvidenceChanged(false)
    try {
      const receipt = await researchDecisions.save(
        experimentId,
        target,
        { ...data, request_id: request.current.id },
        abort.signal
      )
      if (abort.signal.aborted) return
      client.setQueryData<DecisionContext>(key, (old) =>
        old ? { ...old, current: receipt.decision } : old
      )
      void client.invalidateQueries({ queryKey: decisionKey(owner, experimentId) })
      setOpen(false)
      setDraft(null)
      onSaved?.(receipt.decision.id, receipt.event.id)
    } catch (cause) {
      if (!abort.signal.aborted) {
        setError(decisionError(cause))
        setConflict(decisionConflict(cause))
        setEvidenceChanged(decisionEvidenceChanged(cause))
      }
    } finally {
      if (!abort.signal.aborted) {
        controller.current = null
        setBusy(false)
      }
    }
  }
  async function reload() {
    const generation = dialogGeneration.current
    const refreshed = await context.refetch()
    if (opened.current && generation === dialogGeneration.current && refreshed.data) {
      setDraft(initial(refreshed.data, preferredEvaluationId))
      setError(null)
      setConflict(false)
      setEvidenceChanged(false)
      request.current = null
    }
  }
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        aria-label={`Decision for ${name}`}
        onClick={() => close(true)}
      >
        {current ? decisionLabels[current.current.state] : 'Decision'}
      </Button>
      <Dialog open={open} onOpenChange={close}>
        <DialogContent
          className={
            preview
              ? 'max-h-[92dvh] overflow-y-auto sm:max-w-6xl'
              : 'max-h-[90dvh] overflow-y-auto sm:max-w-lg'
          }
        >
          <DialogHeader>
            <DialogTitle>{preview ? 'Later report' : `Decision · ${name}`}</DialogTitle>
            <DialogDescription>
              {preview
                ? 'Saved later results for this candidate.'
                : 'Save your choice and an optional reason.'}
            </DialogDescription>
          </DialogHeader>
          {preview ? (
            <div className="space-y-4">
              <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>
                Back to decision
              </Button>
              {previewQuery.isPending && (
                <p className="text-sm text-muted-foreground">Opening saved report…</p>
              )}
              {previewQuery.isError && (
                <p role="alert" className="text-sm">
                  The saved report could not be opened.{' '}
                  <Button variant="link" onClick={() => void previewQuery.refetch()}>
                    Retry
                  </Button>
                </p>
              )}
              {previewQuery.data &&
                (!previewQuery.data.available ||
                  !previewQuery.data.job ||
                  !previewQuery.data.result) && (
                  <p className="text-sm text-muted-foreground">
                    {previewQuery.data.error ?? 'The saved report is unavailable.'}
                  </p>
                )}
              {previewQuery.data?.available &&
                previewQuery.data.job &&
                previewQuery.data.result && (
                  <FrozenDecisionReport
                    owner={owner}
                    experimentId={experimentId}
                    identity={preview.id}
                    name={name}
                    job={previewQuery.data.job}
                    result={previewQuery.data.result}
                    intent={preview.intent}
                  />
                )}
            </div>
          ) : (
            <>
              {context.isPending && (
                <p className="text-sm text-muted-foreground">Loading decision…</p>
              )}
              {context.isError && (
                <p role="alert" className="text-sm">
                  {decisionEvidenceChanged(context.error)
                    ? decisionError(context.error)
                    : 'Decision could not be loaded.'}{' '}
                  <Button
                    variant="link"
                    onClick={() =>
                      decisionEvidenceChanged(context.error)
                        ? window.location.reload()
                        : void context.refetch()
                    }
                  >
                    {decisionEvidenceChanged(context.error) ? 'Reload report' : 'Retry'}
                  </Button>
                </p>
              )}
              {draft && (
                <form
                  className="space-y-5"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void save()
                  }}
                >
                  <fieldset disabled={archived || busy} className="flex gap-2">
                    <legend className="sr-only">Decision</legend>
                    {Object.entries(decisionLabels).map(([value, label]) => (
                      <Button
                        key={value}
                        type="button"
                        variant={draft.state === value ? 'default' : 'outline'}
                        aria-pressed={draft.state === value}
                        onClick={() => setDraft({ ...draft, state: value as DecisionState })}
                      >
                        {label}
                      </Button>
                    ))}
                  </fieldset>
                  <div className="space-y-2">
                    <Label htmlFor="decision-reason">
                      Reason <span className="font-normal text-muted-foreground">(optional)</span>
                    </Label>
                    <Textarea
                      id="decision-reason"
                      value={draft.reason}
                      maxLength={2000}
                      disabled={archived || busy}
                      onChange={(event) => setDraft({ ...draft, reason: event.target.value })}
                      rows={3}
                    />
                  </div>
                  {(receipts.length > 0 || previousEvaluation || offset > 0) && (
                    <div className="space-y-2">
                      <Label htmlFor="decision-evidence">Supporting report</Label>
                      <select
                        id="decision-evidence"
                        className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                        disabled={archived || busy}
                        value={draft.evaluation}
                        onChange={(event) => setDraft({ ...draft, evaluation: event.target.value })}
                      >
                        <option value="">
                          {'job_id' in target ? 'Selection report only' : 'Comparison report only'}
                        </option>
                        {missingSelected && (
                          <option value={draft.evaluation}>Previously selected later report</option>
                        )}
                        {receipts.map((item) => (
                          <option key={item.id} value={item.id} disabled={!item.available}>
                            Later · {item.dates.from ?? 'Unknown start'} –{' '}
                            {item.dates.to ?? 'Unknown end'}
                            {!item.available ? ' · Unavailable' : ''}
                          </option>
                        ))}
                      </select>
                      {draft.evaluation && (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            setPreview({
                              id: draft.evaluation,
                              intent: {
                                request_id: crypto.randomUUID(),
                                target: decisionOpeningTarget(target, draft.evaluation),
                              },
                            })
                          }
                        >
                          Open later report
                        </Button>
                      )}
                      {(offset > 0 || context.data?.evaluation.next_offset != null) && (
                        <div className="flex gap-2">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy || offset === 0}
                            onClick={() => setOffset(Math.max(0, offset - 20))}
                          >
                            Previous results
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy || context.data?.evaluation.next_offset == null}
                            onClick={() => setOffset(context.data!.evaluation.next_offset!)}
                          >
                            More results
                          </Button>
                        </div>
                      )}
                    </div>
                  )}
                  {context.data && <EvidenceUse evidence={context.data.evidence_use} />}
                  {error && (
                    <div role="alert" className="space-y-1 text-sm text-destructive">
                      <p>{error}</p>
                      {conflict && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            evidenceChanged ? window.location.reload() : void reload()
                          }
                        >
                          {evidenceChanged ? 'Reload report' : 'Reload saved decision'}
                        </Button>
                      )}
                    </div>
                  )}
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    {current ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          close(false)
                          onHistory(current.id, current.current.id)
                        }}
                      >
                        View history
                      </Button>
                    ) : (
                      <span />
                    )}
                    {!archived && (
                      <Button type="submit" disabled={!draft.state || busy || conflict}>
                        {busy ? 'Saving…' : 'Save decision'}
                      </Button>
                    )}
                  </div>
                </form>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
