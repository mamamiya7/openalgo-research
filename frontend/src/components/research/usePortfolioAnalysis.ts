import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type BenchmarkRequest,
  type PortfolioAnalysisStatus,
  type PortfolioResult,
  portfolioResearch,
} from '@/api/portfolioResearch'
import { useAuthStore } from '@/stores/authStore'

export function usePortfolioAnalysis(
  id: string,
  initialResult: PortfolioResult,
  enabled: boolean,
  laterPeriod: boolean,
  freezeAnalysis = false
) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const identity = JSON.stringify([owner, id, laterPeriod])
  const activeIdentity = useRef(identity)
  activeIdentity.current = identity
  const [response, setResponse] = useState<PortfolioAnalysisStatus>({ status: 'missing' })
  const [loaded, setLoaded] = useState<{ id: string; result: PortfolioResult } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const generation = useRef(0)
  const alive = useRef(true)
  const frozen = useRef(freezeAnalysis)
  const pending = useRef<{
    parameters?: string[]
    symbol?: string
    benchmark?: BenchmarkRequest
    marketConditions?: boolean
  } | null>(null)
  const submission = useRef(false)
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
      if (activeIdentity.current !== identity || frozen.current) return
      const benchmark = value.benchmark ?? pending.current?.benchmark
      const marketConditions = value.market_conditions || pending.current?.marketConditions
      if (value.benchmark)
        pending.current = { ...pending.current, benchmark: { ...value.benchmark } }
      if (value.market_conditions) pending.current = { ...pending.current, marketConditions: true }
      setResponse({
        ...value,
        ...(marketConditions
          ? { requested_market_conditions: true }
          : benchmark
            ? { requested_benchmark: benchmark }
            : {}),
      })
      if (value.status === 'complete' && value.job?.result)
        setLoaded({ id: identity, result: value.job.result })
    },
    [identity]
  )
  useEffect(() => {
    generation.current += 1
    setResponse({ status: 'missing' })
    setLoaded((value) => (!freezeAnalysis && value?.id === identity ? value : null))
    setSubmitting(false)
    submission.current = false
    pending.current = null
  }, [identity, freezeAnalysis])
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
              ...(pending.current?.marketConditions
                ? { requested_market_conditions: true }
                : pending.current?.benchmark
                  ? { requested_benchmark: pending.current.benchmark }
                  : {}),
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
  const prepare = async (
    parameters?: string[],
    symbol?: string,
    benchmark?: BenchmarkRequest,
    marketConditions?: boolean
  ) => {
    if (busy || frozen.current || submission.current || activeIdentity.current !== identity) return
    const retry =
      response.status === 'failed' &&
      parameters === undefined &&
      symbol === undefined &&
      benchmark === undefined &&
      marketConditions === undefined
    const request =
      retry && pending.current
        ? pending.current
        : {
            parameters: parameters ? [...parameters] : undefined,
            symbol,
            benchmark: benchmark ? { ...benchmark } : undefined,
            marketConditions,
          }
    pending.current = request
    submission.current = true
    const current = ++generation.current
    setSubmitting(true)
    setResponse({
      status: 'queued',
      ...(request.marketConditions
        ? { requested_market_conditions: true }
        : request.benchmark
          ? { requested_benchmark: request.benchmark }
          : {}),
    })
    try {
      const args = [
        id,
        request.parameters,
        request.symbol,
        laterPeriod ? 'validation' : 'selection',
      ] as const
      const value = await (request.marketConditions
        ? portfolioResearch.prepareAnalysis(...args, request.benchmark, true)
        : request.benchmark
          ? portfolioResearch.prepareAnalysis(...args, request.benchmark)
          : portfolioResearch.prepareAnalysis(...args))
      if (alive.current && generation.current === current) receive(value)
    } catch {
      if (alive.current && generation.current === current) {
        setResponse({
          status: 'failed',
          error: request.marketConditions
            ? 'Could not prepare market conditions. Your saved report is unchanged.'
            : request.benchmark
              ? 'Could not prepare the benchmark. Your saved report is unchanged.'
              : 'Could not prepare analysis. Please try again.',
          ...(request.marketConditions
            ? { requested_market_conditions: true }
            : request.benchmark
              ? { requested_benchmark: request.benchmark }
              : {}),
        })
      }
    } finally {
      if (alive.current && generation.current === current) {
        submission.current = false
        setSubmitting(false)
      }
    }
  }
  const fullResult = !freezeAnalysis && loaded?.id === identity ? loaded.result : undefined
  const result = (laterPeriod ? fullResult?.validation?.result : fullResult) ?? initialResult
  return {
    result: freezeAnalysis ? initialResult : result,
    busy,
    response: freezeAnalysis ? { status: 'complete' as const } : response,
    prepare,
  }
}
