import type { PortfolioResult } from '@/api/portfolioResearch'

const number = (value: unknown, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`
    : '—'

export function ConditionReplaySummary({ result }: { result: PortfolioResult }) {
  const saved = result.condition_replay
  if (!saved) return null
  const strategy = result.strategies.find((value) => value.id === saved.condition.strategy_id)
  return (
    <section aria-label="Condition test" className="min-w-0 space-y-4 border-b pb-5">
      <div className="space-y-1">
        <h3 className="font-semibold">Condition test · {saved.condition.label}</h3>
        <p className="text-sm text-muted-foreground">
          {strategy?.name ?? saved.condition.strategy_id} · Same saved period, prices and settings
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm tabular-nums">
          <caption className="sr-only">
            Unfiltered portfolio versus condition-filtered portfolio
          </caption>
          <thead className="text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="pb-2 font-medium">
                Portfolio
              </th>
              <th scope="col" className="px-3 pb-2 text-right font-medium">
                Return
              </th>
              <th scope="col" className="px-3 pb-2 text-right font-medium">
                Max drawdown
              </th>
              <th scope="col" className="pb-2 pl-3 text-right font-medium">
                Closed trades
              </th>
            </tr>
          </thead>
          <tbody>
            {[
              ['Unfiltered', saved.baseline.summary],
              ['With condition', result.summary],
            ].map(([label, metrics]) => {
              const values = metrics as PortfolioResult['summary']
              return (
                <tr key={label as string} className="border-t">
                  <th scope="row" className="py-3 font-medium">
                    {label as string}
                  </th>
                  <td className="px-3 py-3 text-right">{number(values.net_return_pct, '%')}</td>
                  <td className="px-3 py-3 text-right">{number(values.max_drawdown_pct, '%')}</td>
                  <td className="py-3 pl-3 text-right">{number(values.closed_trades)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        A historical comparison; this condition has not been selected or validated automatically.
      </p>
      <details className="text-xs text-muted-foreground">
        <summary className="w-fit cursor-pointer py-1">Condition test details</summary>
        <div className="mt-3 space-y-2">
          <p>
            {number(saved.counts.allowed)} signals kept · {number(saved.counts.filtered)} skipped by
            condition · {number(saved.counts.unknown)} unclassified
          </p>
          <p>
            {number(saved.counts.unaffected)} signals from other strategies unchanged ·{' '}
            {number(saved.counts.original_excluded)} existing exclusions ·{' '}
            {number(saved.counts.pending)} pending
          </p>
          {saved.basis.map((text, index) => (
            <p key={`${index}-${text}`}>{text}</p>
          ))}
        </div>
      </details>
    </section>
  )
}
