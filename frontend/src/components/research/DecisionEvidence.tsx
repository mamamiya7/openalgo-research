import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { PortfolioJob, PortfolioResult } from '@/api/portfolioResearch'
import {
  decisionKey,
  type EvidenceOpeningIntent,
  type EvidenceUseSummary,
  researchDecisions,
} from '@/api/researchDecisions'
import { Button } from '@/components/ui/button'
import { PortfolioResults } from './PortfolioResults'
import { ReportCurrency } from './ReportCurrency'

export const decisionDate = (value: number) =>
  new Date(value * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
export const decisionLabels = { keep: 'Keep', reject: 'Reject', revisit: 'Revisit' } as const

export function EvidenceUse({ evidence }: { evidence: EvidenceUseSummary }) {
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer py-2">Evidence history</summary>
      <ul className="space-y-2 py-2">
        {evidence.reservation && (
          <li>
            Reserved in this setup: {evidence.reservation.from} – {evidence.reservation.to}
          </li>
        )}
        <li>
          {evidence.calculation === 'recorded'
            ? 'Calculation recorded'
            : evidence.calculation === 'not_recorded'
              ? 'No calculation recorded'
              : 'Earlier calculation history unavailable'}
        </li>
        {evidence.opened_at !== null && <li>Opened {decisionDate(evidence.opened_at)}</li>}
        {evidence.later_used_for_decision && <li>Later results have been used in decisions</li>}
        {evidence.coverage === 'legacy_unknown' && <li>Earlier opening history unavailable</li>}
        {evidence.overlap === 'recorded' && <li>Reserved dates appear in recorded calculations</li>}
      </ul>
    </details>
  )
}

// The caller supplies an intent only from an explicit report action. Reading a URL,
// prefetching, or restoring a report never creates an opening observation.
export function EvidenceOpened({
  owner,
  experimentId,
  intent,
}: {
  owner: string
  experimentId: string
  intent: EvidenceOpeningIntent | null
}) {
  const client = useQueryClient()
  const [retry, setRetry] = useState(0)
  const [failed, setFailed] = useState(false)
  const completed = useRef<string | null>(null)
  const identity = intent ? JSON.stringify([owner, experimentId, intent]) : null
  useEffect(() => {
    // Retry is an explicit user action; retain the original intent and token.
    void retry
    setFailed(false)
    if (!intent || !identity || completed.current === identity) return
    const controller = new AbortController()
    let started = false
    const acknowledge = () => {
      if (started || document.visibilityState !== 'visible') return
      started = true
      researchDecisions
        .opened(experimentId, intent, controller.signal)
        .then(() => {
          if (controller.signal.aborted) return
          completed.current = identity
          void client.invalidateQueries({ queryKey: decisionKey(owner, experimentId) })
        })
        .catch(() => {
          if (!controller.signal.aborted) setFailed(true)
        })
    }
    document.addEventListener('visibilitychange', acknowledge)
    acknowledge()
    return () => {
      controller.abort()
      document.removeEventListener('visibilitychange', acknowledge)
    }
  }, [identity, retry, client, owner, experimentId, intent])
  if (!failed) return null
  return (
    <p className="text-xs text-muted-foreground">
      Opening history could not be saved.{' '}
      <Button variant="link" size="sm" onClick={() => setRetry((value) => value + 1)}>
        Retry recording opening
      </Button>
    </p>
  )
}

export function FrozenDecisionReport({
  owner,
  experimentId,
  identity,
  name,
  job,
  result,
  intent,
}: {
  owner: string
  experimentId: string
  identity: string
  name: string
  job: PortfolioJob
  result: PortfolioResult
  intent: EvidenceOpeningIntent | null
}) {
  const basis = result.report_context?.evaluation_basis
  const currency =
    basis?.status === 'verified' &&
    typeof basis.comparison.currency === 'string' &&
    basis.comparison.currency
      ? basis.comparison.currency
      : null
  return (
    <>
      <p className="text-xs text-muted-foreground">
        {name} · Saved report{!currency ? ' · Currency unrecorded' : ''}
      </p>
      <ReportCurrency.Provider value={currency}>
        <PortfolioResults
          key={JSON.stringify([identity, result.report_context])}
          job={job}
          result={result}
          readOnly
          freezeAnalysis
          embedded
          exportUrl=""
          onRerun={() => undefined}
          rerunning={false}
        />
      </ReportCurrency.Provider>
      <EvidenceOpened owner={owner} experimentId={experimentId} intent={intent} />
    </>
  )
}
