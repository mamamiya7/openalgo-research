import { useEffect, useRef, useState } from 'react'
import { type ConditionSelection, portfolioResearch } from '@/api/portfolioResearch'
import { researchError } from '@/hooks/useResearchExperiment'
import { useAuthStore } from '@/stores/authStore'

export interface ConditionReplayActions {
  busy: boolean
  pending: string | null
  error: string | null
  test: (condition: ConditionSelection) => Promise<void>
}
export const conditionSelectionKey = (condition: ConditionSelection) =>
  JSON.stringify([condition.strategy_id, condition.dimension, condition.regime])

export function useConditionReplay(
  jobId: string,
  experimentId: string | undefined,
  analysisArtifact: string | null | undefined,
  period: 'selection' | 'validation',
  onOpenReport: ((id: string) => void) | undefined,
  disabled: boolean
): ConditionReplayActions {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const identity = JSON.stringify([owner, jobId, experimentId, analysisArtifact, period, disabled])
  const currentIdentity = useRef(identity)
  currentIdentity.current = identity
  const request = useRef<AbortController | null>(null)
  const tokens = useRef(new Map<string, string>())
  const [state, setState] = useState<{
    identity: string
    pending: string | null
    error: string | null
  }>({
    identity,
    pending: null,
    error: null,
  })
  useEffect(() => {
    tokens.current.clear()
    request.current = null
    setState({ identity, pending: null, error: null })
    return () => {
      request.current?.abort()
      request.current = null
    }
  }, [identity])
  async function test(condition: ConditionSelection) {
    if (
      disabled ||
      !experimentId ||
      !analysisArtifact ||
      !onOpenReport ||
      request.current ||
      currentIdentity.current !== identity
    )
      return
    const controller = new AbortController()
    request.current = controller
    const key = conditionSelectionKey(condition)
    const token = tokens.current.get(key) ?? crypto.randomUUID()
    tokens.current.set(key, token)
    const active = () => !controller.signal.aborted && currentIdentity.current === identity
    setState({ identity, pending: key, error: null })
    try {
      const job = await portfolioResearch.conditionReplay(
        jobId,
        {
          ...condition,
          experiment_id: experimentId,
          analysis_artifact: analysisArtifact,
          period,
          request_id: token,
        },
        controller.signal
      )
      if (active()) onOpenReport(job.id)
    } catch (error) {
      if (active()) setState({ identity, pending: null, error: researchError(error) })
    } finally {
      if (request.current === controller) request.current = null
      if (active()) setState((value) => ({ ...value, pending: null }))
    }
  }
  const visible = state.identity === identity ? state : { pending: null, error: null }
  return { busy: visible.pending !== null, pending: visible.pending, error: visible.error, test }
}
