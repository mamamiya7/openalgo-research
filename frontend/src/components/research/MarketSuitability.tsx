import type { MarketSuitabilityFindings, MarketSuitabilityPeriod } from '@/api/portfolioResearch'
import { researchDates } from './researchPresentation'

const number = (value: number | null, suffix = '') =>
  value !== null && Number.isFinite(value)
    ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`
    : '—'

function Coverage({ period }: { period: MarketSuitabilityPeriod }) {
  const coverage = period.coverage
  return (
    <p className="text-xs text-muted-foreground">
      {number(coverage.classified_trades)} of {number(coverage.closed_trades)} closed trades
      classified · {number(coverage.classified_sessions)} of {number(coverage.total_sessions)}{' '}
      sessions
      {coverage.unclassified_trades > 0 &&
        ` · ${number(coverage.unclassified_trades)} trades unclassified`}
    </p>
  )
}

function Period({ period, final = false }: { period: MarketSuitabilityPeriod; final?: boolean }) {
  const multipleStrategies = new Set(period.cohorts.map((row) => row.strategy_id)).size > 1
  return (
    <section
      aria-label={final ? 'Final market conditions' : 'Development market conditions'}
      className="space-y-2"
    >
      <h5 className="text-xs font-medium">
        {final ? 'Final check · kept separate' : 'Development'} ·{' '}
        {researchDates(period.dates.from, period.dates.to)}
      </h5>
      {final && <Coverage period={period} />}
      {period.cohorts.length === 0 ? (
        <p className="text-xs text-muted-foreground">No closed trades to compare.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs tabular-nums">
            <caption className="sr-only">
              {final ? 'Final check' : 'Development'} entry-condition cohorts
            </caption>
            <thead className="text-muted-foreground">
              <tr>
                <th scope="col" className="py-2 pr-3 font-medium">
                  Condition
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Trades
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Entry dates
                </th>
                <th scope="col" className="py-2 pl-3 text-right font-medium">
                  Avg net trade return
                </th>
              </tr>
            </thead>
            <tbody>
              {period.cohorts.map((row) => (
                <tr
                  key={`${row.strategy_id}-${row.dimension}-${row.regime}`}
                  className="border-t border-border/60"
                >
                  <th
                    scope="row"
                    className="py-2 pr-3 font-normal"
                    aria-label={`${multipleStrategies ? `${row.strategy_name} · ` : ''}${row.label}${row.evidence === 'limited' ? ' · Limited sample' : ''}`}
                  >
                    {multipleStrategies && (
                      <span className="block text-muted-foreground">{row.strategy_name} </span>
                    )}
                    {row.label}
                    {row.evidence === 'limited' && (
                      <span className="block text-muted-foreground"> Limited sample</span>
                    )}
                  </th>
                  <td className="px-3 py-2 text-right">{number(row.closed_trades)}</td>
                  <td className="px-3 py-2 text-right">{number(row.entry_sessions)}</td>
                  <td className="py-2 pl-3 text-right">
                    {number(row.average_net_return_pct, '%')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function MarketSuitability({ suitability }: { suitability?: MarketSuitabilityFindings }) {
  if (!suitability || suitability.version !== 'research-market-suitability-v1') return null
  const development = suitability.development
  return (
    <section
      aria-label="Market suitability"
      className="mt-4 space-y-2 border-t border-border/60 pt-3"
    >
      <h4 className="text-sm font-medium">Market suitability</h4>
      <p className="text-sm">
        {development
          ? development.finding.status === 'observed'
            ? development.finding.text
            : 'Not enough evidence to distinguish market conditions.'
          : suitability.reason || 'Market conditions are unavailable for this saved run.'}
      </p>
      {development && <Coverage period={development} />}
      <p className="text-xs text-muted-foreground">Historical association, not a trading filter.</p>
      {(development || suitability.final || suitability.limitations.length > 0) && (
        <details className="text-xs">
          <summary
            tabIndex={0}
            className="w-fit cursor-pointer py-1 text-muted-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          >
            View condition breakdown
          </summary>
          <div className="mt-3 space-y-4">
            {development && <Period period={development} />}
            {suitability.final && <Period period={suitability.final} final />}
            {suitability.final && (
              <p className="text-muted-foreground">
                Final-period outcomes do not choose settings or the development finding.
              </p>
            )}
            {suitability.limitations.map((limitation) => (
              <p key={limitation} className="text-muted-foreground">
                {limitation}
              </p>
            ))}
          </div>
        </details>
      )}
    </section>
  )
}
