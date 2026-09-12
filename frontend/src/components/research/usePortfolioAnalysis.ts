import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type PortfolioAnalysisStatus,
  type PortfolioResult,
  portfolioResearch,
} from '@/api/portfolioResearch'

export function usePortfolioAnalysis(
  id: string,
  initialResult: PortfolioResult,
  enabled: boolean,
  laterPeriod: boolean,
  freezeAnalysis = false
) {
  const [response, setResponse] = useState<PortfolioAnalysisStatus>({ status: 'missing' })
  const [loaded, setLoaded] = useState<{ id: string; result: PortfolioResult } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const generation = useRef(0)
  const alive = useRef(true)
  const frozen = useRef(freezeAnalysis)
  frozen.current = freezeAnalysis
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      generation.current += 1
    }
  }, [])
  const receive = useCallback(
    (value: PortfolioAnalysisStatus) => {
      setResponse(value)
      if (value.job?.result) setLoaded({ id, result: value.job.result })
    },
    [id]
  )
  useEffect(() => {
    generation.current += 1
    setResponse({ status: 'missing' })
    setLoaded((value) => (!freezeAnalysis && value?.id === id ? value : null))
    setSubmitting(false)
  }, [id, freezeAnalysis])
  useEffect(() => {
    if (!enabled || freezeAnalysis) return
    const controller = new AbortController()
    const current = generation.current
    portfolioResearch.analysis(id, controller.signal).then(
      (value) => {
        if (!controller.signal.aborted && generation.current === current) receive(value)
      },
      () => {
        // A saved tear sheet remains readable when a status request is unavailable.
      }
    )
    return () => controller.abort()
  }, [id, enabled, receive, freezeAnalysis])
  const busy =
    !freezeAnalysis && (submitting || response.status === 'queued' || response.status === 'running')
  useEffect(() => {
    if (freezeAnalysis || !busy || submitting) return
    const controller = new AbortController()
    const current = generation.current
    let timer: ReturnType<typeof setTimeout>
    const poll = () => {
      portfolioResearch.analysis(id, controller.signal).then(
        (value) => {
          if (!controller.signal.aborted && generation.current === current) {
            receive(value)
            if (value.status === 'queued' || value.status === 'running')
              timer = setTimeout(poll, 1500)
          }
        },
        () => {
          if (!controller.signal.aborted && generation.current === current) {
            setResponse({
              status: 'failed',
              error: 'Could not check analysis progress. Try again.',
            })
          }
        }
      )
    }
    timer = setTimeout(poll, 1500)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [busy, submitting, id, receive, freezeAnalysis])
  const prepare = async (parameters?: string[], symbol?: string) => {
    if (busy || frozen.current) return
    const current = ++generation.current
    setSubmitting(true)
    setResponse({ status: 'queued' })
    try {
      const value = await portfolioResearch.prepareAnalysis(
        id,
        parameters,
        symbol,
        laterPeriod ? 'validation' : 'selection'
      )
      if (alive.current && generation.current === current) receive(value)
    } catch {
      if (alive.current && generation.current === current) {
        setResponse({ status: 'failed', error: 'Could not prepare analysis. Please try again.' })
      }
    } finally {
      if (alive.current && generation.current === current) setSubmitting(false)
    }
  }
  const fullResult = !freezeAnalysis && loaded?.id === id ? loaded.result : undefined
  const result = (laterPeriod ? fullResult?.validation?.result : fullResult) ?? initialResult
  return {
    result: freezeAnalysis ? initialResult : result,
    busy,
    response: freezeAnalysis ? { status: 'complete' as const } : response,
    prepare,
  }
}
