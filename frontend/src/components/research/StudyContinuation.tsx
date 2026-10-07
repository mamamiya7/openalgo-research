import { useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { Plus } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import {
  researchStudyContinuation,
  type StudyContinuationContext,
  type StudyContinuationRequest,
} from '@/api/researchStudyContinuation'
import { Button } from '@/components/ui/button'
import { researchError } from '@/hooks/useResearchExperiment'
import { researchReturnLocation } from '@/hooks/useResearchNavigation'
import { useResearchWorkspaceMutations } from '@/hooks/useResearchWorkspaceMutations'
import { useAuthStore } from '@/stores/authStore'
import { StudyContinuationDialog } from './StudyContinuationDialog'

export function StudyContinuation({
  experimentId,
  jobId,
  resultArtifact,
  studyName,
  readOnly,
}: {
  experimentId: string
  jobId: string
  resultArtifact: string
  studyName: string
  readOnly: boolean
}) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const client = useQueryClient()
  const [params, setParams] = useSearchParams()
  const { beforeChange, afterChoice } = useResearchWorkspaceMutations()
  const [context, setContext] = useState<StudyContinuationContext | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const active = useRef<AbortController | null>(null)
  const request = useRef<StudyContinuationRequest | null>(null)
  const accepted = useRef<string | null>(null)
  useEffect(() => () => active.current?.abort(), [])
  // biome-ignore lint/correctness/useExhaustiveDependencies: abort state belongs to this exact account and saved study identity.
  useEffect(() => {
    active.current?.abort()
    active.current = null
    request.current = null
    accepted.current = null
    setOpen(false)
    setBusy(false)
    setContext(null)
    setError(null)
  }, [experimentId, jobId, resultArtifact, owner])
  useEffect(() => {
    if (readOnly) {
      active.current?.abort()
      active.current = null
      setBusy(false)
      setOpen(false)
    }
  }, [readOnly])

  async function begin() {
    if (readOnly || active.current) return
    if (request.current && context) {
      setOpen(true)
      return
    }
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      await beforeChange()
      if (controller.signal.aborted) return
      const data = await researchStudyContinuation.context(experimentId, jobId, controller.signal)
      if (controller.signal.aborted) return
      if (data.parent_result_artifact !== resultArtifact)
        throw new Error('Reopen this study before adding trials.')
      if (!data.available) throw new Error(data.reason ?? 'This study cannot add more trials.')
      setContext(data)
      setOpen(true)
    } catch (cause) {
      if (!controller.signal.aborted) setError(researchError(cause))
    } finally {
      if (active.current === controller) {
        active.current = null
        setBusy(false)
      }
    }
  }
  function close(value: boolean) {
    active.current?.abort()
    active.current = null
    setBusy(false)
    setOpen(value)
  }
  async function extend(additional: number) {
    if (readOnly || active.current || !context) return
    if (request.current && request.current.additional_trials !== additional) {
      // An uncertain response is retried with the identical body. Changing the
      // amount must not create another study while the first may be accepted.
      setError('Retry the original trial count to recover the saved request.')
      return
    }
    request.current ??= {
      revision: context.revision,
      request_id: crypto.randomUUID(),
      parent_result_artifact: resultArtifact,
      additional_trials: additional,
    }
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      if (!accepted.current) {
        const response = await researchStudyContinuation.extend(
          experimentId,
          jobId,
          request.current,
          controller.signal
        )
        if (controller.signal.aborted) return
        accepted.current = response.job.id
      }
      await afterChoice()
      if (controller.signal.aborted) return
      void client.invalidateQueries({ queryKey: ['research-library', owner] })
      void client.invalidateQueries({ queryKey: ['research-experiment', owner, experimentId] })
      setParams({
        experiment: experimentId,
        view: 'studies',
        job: accepted.current!,
        return_research: researchReturnLocation(params, experimentId),
      })
      setOpen(false)
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (
          !accepted.current &&
          isAxiosError(cause) &&
          [400, 404, 409].includes(cause.response?.status ?? 0)
        ) {
          request.current = null
          setOpen(false)
        }
        setError(researchError(cause))
      }
    } finally {
      if (active.current === controller) {
        active.current = null
        setBusy(false)
      }
    }
  }
  return (
    <div className="space-y-2">
      <Button variant="outline" disabled={readOnly || busy} onClick={() => void begin()}>
        <Plus className="size-4" aria-hidden="true" />
        {busy && !open ? 'Opening study…' : 'Add trials'}
      </Button>
      {!open && error && (
        <p role="alert" className="max-w-sm text-xs text-destructive">
          {error}
        </p>
      )}
      {context && (
        <StudyContinuationDialog
          open={open}
          onOpenChange={close}
          currentProposed={context.current_proposed}
          maxTotal={context.max_total}
          studyName={studyName}
          initialAdditionalTrials={request.current?.additional_trials}
          amountLocked={Boolean(request.current)}
          pending={busy}
          error={error}
          onConfirm={(value) => void extend(value)}
        />
      )}
    </div>
  )
}
