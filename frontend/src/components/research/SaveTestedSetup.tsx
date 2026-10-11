import { useQuery, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { chosenSetupKey, researchChosenSetups } from '@/api/researchChosenSetups'
import {
  type DecisionEvent,
  type DecisionRequest,
  type DecisionTarget,
  decisionKey,
  decisionTargetKey,
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { researchError } from '@/hooks/useResearchExperiment'
import { useResearchWorkspaceMutations } from '@/hooks/useResearchWorkspaceMutations'
import { useAuthStore } from '@/stores/authStore'

/** One user action, retaining independently fenced Keep and Choose requests. */
export function SaveTestedSetup({
  experimentId,
  target,
  name: initialName,
  readOnly,
}: {
  experimentId: string
  target: DecisionTarget
  name: string
  readOnly: boolean
}) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const client = useQueryClient()
  const [, setParams] = useSearchParams()
  const { beforeChange, afterChoice } = useResearchWorkspaceMutations()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(initialName)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [blocked, setBlocked] = useState(false)
  const active = useRef<AbortController | null>(null)
  const kept = useRef<DecisionEvent | null>(null)
  const keepRequest = useRef<DecisionRequest | null>(null)
  const chooseRequest = useRef<{
    decision_id: string
    event_id: string
    revision: number
    name: string
    request_id: string
  } | null>(null)
  const committed = useRef(false)
  const key = [...decisionKey(owner, experimentId), 'save-tested', ...decisionTargetKey(target)]
  const identity = JSON.stringify([owner, experimentId, ...decisionTargetKey(target)])
  const context = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => researchDecisions.context(experimentId, target, 0, signal),
    enabled: open,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })
  useEffect(() => {
    void identity
    kept.current = null
    keepRequest.current = null
    chooseRequest.current = null
    committed.current = false
    setOpen(false)
    setBusy(false)
    setError(null)
    setBlocked(false)
    setName(initialName)
    setNote('')
    return () => {
      active.current?.abort()
      active.current = null
    }
  }, [identity, initialName])
  useEffect(() => {
    if (readOnly) {
      active.current?.abort()
      active.current = null
      setBusy(false)
    }
  }, [readOnly])
  function close(value: boolean) {
    active.current?.abort()
    active.current = null
    setBusy(false)
    setOpen(value)
  }
  async function save() {
    if (
      readOnly ||
      blocked ||
      active.current ||
      !context.data ||
      context.data.archived ||
      !name.trim()
    )
      return
    const abort = new AbortController()
    active.current = abort
    setBusy(true)
    setError(null)
    try {
      await beforeChange()
      if (abort.signal.aborted) return
      if (!kept.current && !committed.current) {
        const current = context.data.current
        if (current?.current.state === 'keep') kept.current = current.current
        else {
          keepRequest.current ??= {
            revision: current?.revision ?? 0,
            request_id: crypto.randomUUID(),
            state: 'keep',
            reason: note.trim(),
            evaluation_id: current?.current.evaluation?.id ?? null,
          }
          const receipt = await researchDecisions.save(
            experimentId,
            target,
            keepRequest.current,
            abort.signal
          )
          if (abort.signal.aborted) return
          kept.current = receipt.event
        }
      }
      if (abort.signal.aborted) return
      if (!committed.current) {
        if (!chooseRequest.current) {
          const eligibility = await researchChosenSetups.context(
            experimentId,
            { decision_id: kept.current!.decision_id, event_id: kept.current!.id },
            abort.signal
          )
          if (abort.signal.aborted) return
          if (eligibility.archived || !eligibility.eligibility?.available) {
            setBlocked(true)
            throw new Error(
              eligibility.eligibility?.reason ??
                'This saved report cannot supply reusable settings.'
            )
          }
          chooseRequest.current = {
            revision: eligibility.revision,
            request_id: crypto.randomUUID(),
            name: name.trim(),
            decision_id: kept.current!.decision_id,
            event_id: kept.current!.id,
          }
        }
        await researchChosenSetups.choose(experimentId, chooseRequest.current, abort.signal)
        if (abort.signal.aborted) return
        committed.current = true
      }
      if (abort.signal.aborted) return
      await afterChoice()
      if (abort.signal.aborted) return
      await Promise.all([
        client.invalidateQueries({ queryKey: decisionKey(owner, experimentId) }),
        client.invalidateQueries({ queryKey: chosenSetupKey(owner, experimentId) }),
        client.invalidateQueries({ queryKey: ['research-library', owner] }),
      ])
      if (abort.signal.aborted) return
      setOpen(false)
      setParams({ experiment: experimentId, view: 'overview' })
    } catch (cause) {
      if (!abort.signal.aborted) {
        if (
          isAxiosError(cause) &&
          ['revision_conflict', 'decision_revision_conflict', 'decision_evidence_changed'].includes(
            cause.response?.data?.code
          )
        )
          setBlocked(true)
        setError(`${kept.current ? 'Your Keep decision is saved. ' : ''}${researchError(cause)}`)
      }
    } finally {
      if (active.current === abort) {
        active.current = null
        setBusy(false)
      }
    }
  }
  return (
    <>
      <Button size="sm" disabled={readOnly} onClick={() => setOpen(true)}>
        Save tested setup
      </Button>
      <Dialog open={open} onOpenChange={close}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Save tested setup</DialogTitle>
            <DialogDescription>
              Keep these exact rules and their saved evidence together for reuse.
            </DialogDescription>
          </DialogHeader>
          {context.isPending ? (
            <output>Checking saved evidence…</output>
          ) : context.isError ? (
            <p role="alert">
              {researchError(context.error)}{' '}
              <Button variant="link" onClick={() => void context.refetch()}>
                Retry
              </Button>
            </p>
          ) : (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault()
                void save()
              }}
            >
              <div className="space-y-2">
                <Label htmlFor="tested-setup-name">Setup name</Label>
                <Input
                  id="tested-setup-name"
                  value={name}
                  maxLength={120}
                  readOnly={busy || Boolean(chooseRequest.current)}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              {context.data?.current?.current.state !== 'keep' && (
                <div className="space-y-2">
                  <Label htmlFor="tested-setup-note">Note (optional)</Label>
                  <Textarea
                    id="tested-setup-note"
                    value={note}
                    maxLength={2000}
                    readOnly={busy || Boolean(keepRequest.current)}
                    onChange={(event) => setNote(event.target.value)}
                  />
                </div>
              )}
              <p className="text-sm text-muted-foreground">
                Records Keep and saves a reusable setup. Trading stays unchanged.
              </p>
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              {blocked ? (
                <Button
                  variant="outline"
                  type="button"
                  onClick={() => {
                    setOpen(false)
                    setParams({ experiment: experimentId, view: 'review' })
                  }}
                >
                  Review saved evidence
                </Button>
              ) : (
                <Button
                  type="submit"
                  disabled={
                    busy || context.isFetching || readOnly || context.data?.archived || !name.trim()
                  }
                >
                  {busy ? 'Saving setup…' : kept.current ? 'Finish saving setup' : 'Save setup'}
                </Button>
              )}
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
