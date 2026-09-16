import { Loader2 } from 'lucide-react'
import type { BenchmarkRequest, PortfolioAnalysis } from '@/api/portfolioResearch'
import { Button } from '@/components/ui/button'
import { AnalysisFigure } from './AnalysisCharts'
import type { AnalysisActions } from './PortfolioAnalysis'
import { researchDates } from './researchPresentation'

export const niftyBenchmark: BenchmarkRequest = {
  symbol: 'NIFTY',
  exchange: 'NSE_INDEX',
  interval: 'D',
  role: 'benchmark',
}
const number = (value: unknown, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`
    : '—'

export function BenchmarkComparison({
  analysis,
  busy,
  response,
  onPrepare,
  readOnly,
  freezeAnalysis,
  conditionReplay,
}: AnalysisActions & { analysis?: PortfolioAnalysis }) {
  const saved = analysis?.benchmark
  const requested = Boolean(response.requested_benchmark)
  const failed = requested && response.status === 'failed'
  const preparing = requested && busy
  const editable = !readOnly && !freezeAnalysis
  const retryable =
    !saved ||
    (saved.descriptor.symbol === 'NIFTY' &&
      saved.descriptor.exchange === 'NSE_INDEX' &&
      saved.descriptor.interval === 'D')
  const start = () => {
    if (!editable || busy || conditionReplay?.busy) return
    if (failed) onPrepare()
    else onPrepare(undefined, undefined, niftyBenchmark)
  }
  if (!saved && !editable && !preparing) return null
  const label = saved?.descriptor.symbol === 'NIFTY' ? 'Nifty 50' : saved?.descriptor.symbol
  const chart = analysis?.charts.find((item) => item.id === 'benchmark-comparison')
  return (
    <section
      aria-label="Market benchmark"
      className={saved ? 'space-y-4 border-t pt-5' : 'space-y-2'}
    >
      {saved && (
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="font-semibold">Against {label}</h3>
          <p className="text-xs text-muted-foreground">
            {researchDates(saved.dates.from, saved.dates.to)} · {number(saved.observations)} matched
            observations
          </p>
        </div>
      )}
      {saved?.status === 'partial' && (
        <p className="text-sm text-muted-foreground">
          Matched dates only · {number(saved.omitted_sessions)} sessions omitted. {saved.reason}
        </p>
      )}
      {saved?.status === 'unavailable' && (
        <p className="text-sm text-muted-foreground">
          {saved.reason ?? 'A matching benchmark is not available for these dates.'}
        </p>
      )}
      {saved && saved.status !== 'unavailable' && (
        <>
          <dl className="grid grid-cols-3 gap-4 text-sm">
            {[
              ['Portfolio', number(saved.metrics.portfolio_return_pct, '%')],
              [label, number(saved.metrics.benchmark_return_pct, '%')],
              ['Difference', number(saved.metrics.excess_return_pct, ' pp')],
            ].map(([title, value]) => (
              <div key={title}>
                <dt className="text-xs text-muted-foreground">{title}</dt>
                <dd className="mt-1 text-lg font-medium tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          {chart?.status === 'available' && chart.figure && (
            <AnalysisFigure chart={chart} height={250} deferred />
          )}
        </>
      )}
      {preparing ? (
        <output className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          Preparing benchmark…
        </output>
      ) : (
        <>
          {failed && (
            <output className="block text-sm text-muted-foreground">
              {response.error ?? 'The benchmark could not be prepared. Your report is unchanged.'}
            </output>
          )}
          {editable && retryable && (!saved || saved.status !== 'available' || failed) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              disabled={busy || conditionReplay?.busy}
              onClick={start}
            >
              {failed || saved ? 'Retry benchmark' : 'Add Nifty 50 benchmark'}
            </Button>
          )}
        </>
      )}
      {saved && (
        <details className="text-xs text-muted-foreground">
          <summary className="w-fit cursor-pointer py-1">Benchmark details</summary>
          <div className="mt-3 space-y-3">
            {saved.status !== 'unavailable' && (
              <dl className="grid gap-3 sm:grid-cols-3">
                {[
                  ['Beta', number(saved.metrics.beta)],
                  ['Alpha', number(saved.metrics.alpha_pct, '%')],
                  ['Correlation', number(saved.metrics.correlation)],
                  ['Tracking error', number(saved.metrics.tracking_error_pct, '%')],
                  ['Information ratio', number(saved.metrics.information_ratio)],
                ].map(([title, value]) => (
                  <div key={title}>
                    <dt>{title}</dt>
                    <dd className="mt-1 tabular-nums text-foreground">{value}</dd>
                  </div>
                ))}
              </dl>
            )}
            <p>
              {saved.descriptor.exchange} ·{' '}
              {saved.descriptor.interval === 'D' ? 'Daily' : saved.descriptor.interval} benchmark
              prices
            </p>
            {saved.basis.map((text, index) => (
              <p key={`${index}-${text}`}>{text}</p>
            ))}
          </div>
        </details>
      )}
    </section>
  )
}
