import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { portfolioResearch } from '@/api/portfolioResearch'
import { researchError } from '@/hooks/useResearchExperiment'
import { useAuthStore } from '@/stores/authStore'
import type { PortfolioDraft } from './PortfolioBuilder'
import { portfolioDraftIssue, portfolioPayload } from './PortfolioBuilder'
import { researchDates } from './researchPresentation'

export function PortfolioSetupReview({ draft }: { draft: PortfolioDraft }) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const request = portfolioDraftIssue(draft) ? '' : JSON.stringify(portfolioPayload(draft))
  const [settled, setSettled] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setSettled(request), 450)
    return () => clearTimeout(timer)
  }, [request])
  const preview = useQuery({
    queryKey: ['research-setup-preview', owner, settled],
    queryFn: ({ signal }) => portfolioResearch.preflight(JSON.parse(settled), signal),
    enabled: Boolean(settled && settled === request),
    staleTime: 30000,
    gcTime: 60000,
    retry: false,
    refetchOnWindowFocus: false,
  })
  if (!request) return null
  // Never show a date or interval from the previous settings while the new plan resolves.
  const current = settled === request ? preview.data : undefined
  const error = settled === request ? preview.error : null
  const plan = current?.period_plan
  const automatic = current?.automatic_plan
  return (
    <section
      className="space-y-1 text-xs text-muted-foreground"
      aria-label="Data and period review"
    >
      {current ? (
        <>
          <p>
            {current.interval === '1m' ? 'Minute' : 'Daily'} candles from OpenAlgo ·{' '}
            {current.receipt.signal_count.toLocaleString('en-IN')} signals ·{' '}
            {researchDates(current.receipt.date_from, current.receipt.date_to)}
          </p>
          {plan && (
            <p>
              Selection signals {researchDates(plan.selection.from, plan.selection.to)} ·{' '}
              {plan.mode === 'reserve' ? 'Reserved signals' : 'Later signals'}{' '}
              {researchDates(plan.evaluation.from, plan.evaluation.to)}
            </p>
          )}
          {automatic && (
            <details className="pt-1">
              <summary className="w-fit cursor-pointer text-foreground">
                What will be tested
              </summary>
              <div className="mt-2 space-y-2">
                <p>
                  {automatic.summary.searched_parameters} settings · up to{' '}
                  {automatic.summary.max_simulations} calculations
                  {automatic.summary.condition_presets > 0
                    ? ` · ${automatic.summary.condition_presets} conditions`
                    : ''}
                </p>
                {automatic.strategies.map((strategy) => (
                  <p key={strategy.id}>
                    <span className="font-medium text-foreground">{strategy.name}</span> ·{' '}
                    {strategy.parameters
                      .filter((parameter) => parameter.status === 'search')
                      .map((parameter) => parameter.label)
                      .join(', ')}
                  </p>
                ))}
                <p>
                  Starting cash, allocation and trade size stay fixed. Prices are prepared once.
                </p>
              </div>
            </details>
          )}
        </>
      ) : (
        <p>{error ? researchError(error) : 'Checking dates and candle interval…'}</p>
      )}
    </section>
  )
}
