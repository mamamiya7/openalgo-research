import { Loader2 } from 'lucide-react'
import { useId, useState } from 'react'
import type {
  ConditionSelection,
  MarketConditionsAnalysis,
  PortfolioAnalysis,
} from '@/api/portfolioResearch'
import { Button } from '@/components/ui/button'
import type { AnalysisActions } from './PortfolioAnalysis'
import { researchDates } from './researchPresentation'
import { conditionSelectionKey } from './useConditionReplay'

type Dimension = 'trend' | 'volatility'
const regimes = {
  trend: {
    up: { label: 'Rising', color: 'bg-teal-500' },
    range: { label: 'Sideways', color: 'bg-slate-400' },
    down: { label: 'Falling', color: 'bg-violet-500' },
    unknown: { label: 'Unclassified', color: 'bg-muted' },
  },
  volatility: {
    low: { label: 'Low', color: 'bg-sky-400' },
    normal: { label: 'Normal', color: 'bg-sky-600' },
    high: { label: 'High', color: 'bg-amber-500' },
    unknown: { label: 'Unclassified', color: 'bg-muted' },
  },
} as const
const number = (value: unknown, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`
    : '—'

function ConditionTimeline({
  dimension,
  timeline,
}: {
  dimension: Dimension
  timeline: MarketConditionsAnalysis['timeline']
}) {
  const labels: Record<string, { label: string; color: string }> = regimes[dimension]
  const runs: Array<{ key: string; from: string; to: string; length: number }> = []
  for (const point of timeline) {
    const key = point[dimension]
    const last = runs.at(-1)
    if (last?.key === key) {
      last.to = point.date
      last.length += 1
    } else runs.push({ key, from: point.date, to: point.date, length: 1 })
  }
  return (
    <div className="min-w-0 space-y-2">
      <p className="text-xs font-medium">{dimension === 'trend' ? 'Trend' : 'Volatility'}</p>
      <ol
        aria-label={`Historical ${dimension}, earliest to latest`}
        className="flex h-3 min-w-0 list-none overflow-hidden rounded-sm p-0"
      >
        {runs.map((run) => {
          const descriptor = labels[run.key] ?? labels.unknown
          const text = `${descriptor.label} · ${researchDates(run.from, run.to)} · ${run.length} sessions`
          return (
            <li
              key={`${run.from}-${run.key}`}
              className={`min-w-0 ${descriptor.color}`}
              style={{ flex: `${run.length} 1 0%` }}
              aria-label={text}
              title={text}
            />
          )
        })}
      </ol>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {Object.entries(labels)
          .filter(([key]) => key !== 'unknown' || runs.some((run) => run.key === key))
          .map(([key, value]) => (
            <span key={key} className="inline-flex items-center gap-1.5">
              <span className={`size-2 rounded-sm ${value.color}`} aria-hidden="true" />
              {value.label}
            </span>
          ))}
      </div>
    </div>
  )
}

export function MarketConditions({
  analysis,
  busy,
  response,
  onPrepare,
  readOnly,
  freezeAnalysis,
  conditionReplay,
}: AnalysisActions & { analysis?: PortfolioAnalysis }) {
  const id = useId()
  const [dimension, setDimension] = useState<Dimension>('trend')
  const [strategy, setStrategy] = useState('')
  const saved = analysis?.market_conditions
  const requested = Boolean(response.requested_market_conditions)
  const failed = requested && response.status === 'failed'
  const preparing = requested && busy
  const editable = !readOnly && !freezeAnalysis
  const canTest = editable && Boolean(conditionReplay)
  const strategies = [
    ...new Map(saved?.cohorts.map((row) => [row.strategy_id, row.strategy_name])).entries(),
  ]
  const selected = strategies.some(([key]) => key === strategy) ? strategy : strategies[0]?.[0]
  const rows = saved?.cohorts.filter(
    (row) => row.strategy_id === selected && row.dimension === dimension
  )
  const start = () => {
    if (!editable || busy || conditionReplay?.busy) return
    if (failed) onPrepare()
    else onPrepare(undefined, undefined, undefined, true)
  }
  if (!saved && !editable && !preparing) return null
  return (
    <section
      aria-label="Market conditions"
      className={saved ? 'min-w-0 space-y-5 border-t pt-5' : 'space-y-2'}
    >
      {saved && (
        <>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-semibold">Market conditions</h3>
            <p className="text-xs text-muted-foreground">
              {saved.descriptor.symbol === 'NIFTY' ? 'Nifty 50' : saved.descriptor.symbol} ·{' '}
              {researchDates(saved.dates.from, saved.dates.to)}
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium">{saved.finding.text}</p>
            <p className="text-xs text-muted-foreground">{saved.finding.next_step}</p>
          </div>
          {saved.status !== 'unavailable' && (
            <>
              <div className="space-y-4">
                <ConditionTimeline dimension="trend" timeline={saved.timeline} />
                <ConditionTimeline dimension="volatility" timeline={saved.timeline} />
              </div>
              <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <fieldset className="flex gap-1">
                    <legend className="sr-only">Group trades by</legend>
                    {(['trend', 'volatility'] as const).map((key) => (
                      <Button
                        key={key}
                        type="button"
                        size="sm"
                        variant={dimension === key ? 'secondary' : 'ghost'}
                        aria-pressed={dimension === key}
                        onClick={() => setDimension(key)}
                      >
                        {key === 'trend' ? 'Trend' : 'Volatility'}
                      </Button>
                    ))}
                  </fieldset>
                  {strategies.length > 1 ? (
                    <div className="flex items-center gap-2 text-xs">
                      <label htmlFor={`${id}-strategy`}>Strategy</label>
                      <select
                        id={`${id}-strategy`}
                        value={selected}
                        onChange={(event) => setStrategy(event.target.value)}
                        className="h-8 max-w-52 rounded-md border bg-background px-2"
                      >
                        {strategies.map(([key, name]) => (
                          <option key={key} value={key}>
                            {name}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">{strategies[0]?.[1]}</p>
                  )}
                </div>
                {rows && rows.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm tabular-nums">
                      <caption className="sr-only">
                        Closed trades grouped by market conditions at entry
                      </caption>
                      <thead className="text-xs text-muted-foreground">
                        <tr>
                          <th scope="col" className="pb-3 pr-3 font-medium">
                            Condition
                          </th>
                          <th scope="col" className="px-2 pb-3 text-right font-medium">
                            Closed trades
                          </th>
                          <th scope="col" className="px-2 pb-3 text-right font-medium">
                            Entry dates
                          </th>
                          <th scope="col" className="px-2 pb-3 text-right font-medium">
                            Avg. net trade return
                          </th>
                          <th scope="col" className="pb-3 pl-2 text-right font-medium">
                            Win rate
                          </th>
                          {canTest && (
                            <th scope="col">
                              <span className="sr-only">Test condition</span>
                            </th>
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row) => (
                          <tr
                            key={`${row.strategy_id}-${row.dimension}-${row.regime}`}
                            className="border-t"
                          >
                            <th scope="row" className="py-3 pr-3 font-medium">
                              {row.label}
                              {row.evidence === 'limited' && row.regime !== 'unknown' && (
                                <span className="mt-1 block text-xs font-normal text-muted-foreground">
                                  Small sample
                                </span>
                              )}
                            </th>
                            <td className="px-2 py-3 text-right">{number(row.closed_trades)}</td>
                            <td className="px-2 py-3 text-right">{number(row.entry_sessions)}</td>
                            <td className="whitespace-nowrap px-2 py-3 text-right">
                              {number(row.average_net_return_pct, '%')}
                            </td>
                            <td className="whitespace-nowrap py-3 pl-2 text-right">
                              {number(row.win_rate_pct, '%')}
                            </td>
                            {canTest && (
                              <td className="py-3 pl-3 text-right">
                                {(row.dimension === 'trend'
                                  ? ['up', 'down', 'range']
                                  : ['normal', 'high']
                                ).includes(row.regime) && (
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    aria-label={`Test ${row.label.toLowerCase()} condition`}
                                    disabled={busy || conditionReplay?.busy}
                                    onClick={() => {
                                      void conditionReplay?.test({
                                        strategy_id: row.strategy_id,
                                        dimension: row.dimension,
                                        regime: row.regime as ConditionSelection['regime'],
                                      })
                                    }}
                                  >
                                    {conditionReplay?.pending ===
                                    conditionSelectionKey(row as ConditionSelection)
                                      ? 'Starting…'
                                      : 'Test condition'}
                                  </Button>
                                )}
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}
          <p className="text-xs text-muted-foreground">
            {number(saved.coverage.classified_sessions)} of {number(saved.coverage.total_sessions)}{' '}
            sessions classified · {number(saved.coverage.classified_trades)} of{' '}
            {number(saved.coverage.closed_trades)} closed trades classified
            {saved.coverage.unclassified_trades > 0 &&
              ` · ${number(saved.coverage.unclassified_trades)} unclassified`}
          </p>
          {conditionReplay?.error && (
            <output className="block text-sm text-destructive">{conditionReplay.error}</output>
          )}
          <details className="text-xs text-muted-foreground">
            <summary className="w-fit cursor-pointer py-1">How conditions were measured</summary>
            <div className="mt-3 space-y-2">
              <p>
                Closed trades grouped by conditions known at entry. Filtering entries requires a new
                portfolio backtest.
              </p>
              {saved.basis.map((text, index) => (
                <p key={`${index}-${text}`}>{text}</p>
              ))}
            </div>
          </details>
        </>
      )}
      {preparing ? (
        <output className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          Preparing market conditions…
        </output>
      ) : (
        <>
          {failed && (
            <output className="block text-sm text-muted-foreground">
              {response.error ??
                'Market conditions could not be prepared. Your report is unchanged.'}
            </output>
          )}
          {editable && (!saved || failed) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              disabled={busy || conditionReplay?.busy}
              onClick={start}
            >
              {failed || saved ? 'Retry market conditions' : 'Analyze market conditions'}
            </Button>
          )}
        </>
      )}
    </section>
  )
}
