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
  laterPeriod: boolean
) {
  const [response, setResponse] = useState<PortfolioAnalysisStatus>({ status: 'missing' })
  const [loaded, setLoaded] = useState<{ id: string; result: PortfolioResult } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const generation = useRef(0)
  const alive = useRef(true)
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
    setLoaded((value) => (value?.id === id ? value : null))
    setSubmitting(false)
  }, [id])
  useEffect(() => {
    if (!enabled) return
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
  }, [id, enabled, receive])
  const busy = submitting || response.status === 'queued' || response.status === 'running'
  useEffect(() => {
    if (!busy || submitting) return
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
  }, [busy, submitting, id, receive])
  const prepare = async (parameters?: string[], symbol?: string) => {
    if (busy) return
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
  const fullResult = loaded?.id === id ? loaded.result : undefined
  const result = (laterPeriod ? fullResult?.validation?.result : fullResult) ?? initialResult
  return { result, busy, response, prepare }
}
