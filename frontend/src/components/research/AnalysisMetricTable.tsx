import { useMemo, useState } from 'react'
import type { AnalysisMetric, ScalarAnalysis } from '@/api/portfolioResearch'
import { Input } from '@/components/ui/input'
import { trialMetricText } from './portfolioTrialMetrics'

export function AnalysisMetricTable({
  catalog,
  analysis,
}: {
  catalog: AnalysisMetric[]
  analysis: ScalarAnalysis
}) {
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState('all')
  const groups = useMemo(() => [...new Set(catalog.map((metric) => metric.group))], [catalog])
  const availableCount = catalog.filter(
    (metric) => trialMetricText(metric, analysis.metrics[metric.key]) !== '—'
  ).length
  const metrics = catalog.filter(
    (metric) =>
      (group === 'all' || metric.group === group) &&
      (!query.trim() ||
        `${metric.label} ${metric.description} ${metric.source} ${metric.group}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()))
  )
  return (
    <section className="space-y-3" aria-label="Performance statistics">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          aria-label="Find a statistic"
          placeholder="Find a statistic…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="max-w-xs"
        />
        <select
          aria-label="Statistic group"
          value={group}
          onChange={(event) => setGroup(event.target.value)}
          className="h-9 max-w-full rounded-md border bg-background px-3 text-sm"
        >
          <option value="all">All groups</option>
          {groups.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {availableCount} available · {catalog.length - availableCount} unavailable
          {metrics.length !== catalog.length ? ` · ${metrics.length} shown` : ''}
        </span>
      </div>
      <div className="max-h-[65vh] overflow-auto rounded-lg border">
        <table className="w-full text-sm">
          <caption className="sr-only">Full performance statistics</caption>
          <thead className="sticky top-0 z-10 bg-muted text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-3 text-left font-medium">
                Statistic
              </th>
              <th scope="col" className="px-4 py-3 text-right font-medium">
                Value
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {metrics.map((metric) => (
              <tr key={metric.key}>
                <th scope="row" className="px-4 py-3 text-left font-normal">
                  <details>
                    <summary className="cursor-pointer font-medium">{metric.label}</summary>
                    <p className="mt-2 max-w-lg text-xs leading-relaxed text-muted-foreground">
                      {metric.description}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {metric.group} · {metric.source}
                    </p>
                    {analysis.unavailable[metric.key] && (
                      <p className="mt-2 max-w-lg text-xs leading-relaxed text-muted-foreground">
                        {analysis.unavailable[metric.key]}
                      </p>
                    )}
                  </details>
                </th>
                <td className="max-w-sm px-4 py-3 text-right tabular-nums">
                  <span title={analysis.unavailable[metric.key]}>
                    {trialMetricText(metric, analysis.metrics[metric.key])}
                  </span>
                </td>
              </tr>
            ))}
            {!metrics.length && (
              <tr>
                <td colSpan={2} className="p-8 text-center text-muted-foreground">
                  No statistics match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
