import { useState } from 'react'
import type { PortfolioAnalysis } from '@/api/portfolioResearch'
import { Button } from '@/components/ui/button'
import type { DrawdownRow } from './reportInvestigationEvidence'

type Drawdowns = NonNullable<PortfolioAnalysis['report_depth']>['drawdowns']
const date = (value: string | null) =>
  value ? value.replace('T', ' ').replace('+05:30', ' IST') : 'Starting capital'

export function ReportDrawdowns({
  drawdowns,
  onInspect,
  expanded: controlledExpanded,
  onExpandedChange,
}: {
  drawdowns: Drawdowns
  onInspect?: (row: DrawdownRow) => void
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
}) {
  const [localExpanded, setLocalExpanded] = useState(false)
  const expanded = controlledExpanded ?? localExpanded
  const setExpanded = (value: boolean) => {
    setLocalExpanded(value)
    onExpandedChange?.(value)
  }
  if (drawdowns.status !== 'available' || !drawdowns.rows.length) return null
  const rows = expanded ? drawdowns.rows : drawdowns.rows.slice(0, 5)
  return (
    <div className="min-w-0 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-sm font-medium">
          {expanded ? 'Drawdown episodes' : 'Worst drawdowns'}
        </h4>
        {drawdowns.rows.length > 5 && (
          <Button size="sm" variant="ghost" onClick={() => setExpanded(!expanded)}>
            {expanded ? 'Show five' : 'View all'}
          </Button>
        )}
      </div>
      <div className="max-h-96 max-w-full overflow-auto rounded-lg border">
        <table className="w-full text-sm">
          <caption className="sr-only">Drawdowns from saved account marks</caption>
          <thead className="sticky top-0 bg-muted">
            <tr>
              {[
                'Peak',
                'Low',
                'Depth',
                'Recovered',
                'Underwater sessions',
                'Recovery sessions',
              ].map((label) => (
                <th
                  key={label}
                  scope="col"
                  className="whitespace-nowrap p-3 text-left text-xs font-medium text-muted-foreground"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="whitespace-nowrap p-3 text-xs">{date(row.peak_at)}</td>
                <td className="whitespace-nowrap p-3 text-xs">{date(row.trough_at)}</td>
                <td className="p-3 tabular-nums">
                  {onInspect ? (
                    <Button
                      variant="link"
                      size="sm"
                      className="h-auto p-0 tabular-nums"
                      aria-label={`Inspect drawdown ${row.id}`}
                      onClick={() => onInspect(row)}
                    >
                      {row.depth_pct.toLocaleString('en-IN', { maximumFractionDigits: 2 })}%
                    </Button>
                  ) : (
                    <>{row.depth_pct.toLocaleString('en-IN', { maximumFractionDigits: 2 })}%</>
                  )}
                </td>
                <td className="whitespace-nowrap p-3 text-xs">
                  {row.recovered_at ? date(row.recovered_at) : 'Ongoing'}
                </td>
                <td className="p-3 tabular-nums">{row.underwater_sessions}</td>
                <td className="p-3 tabular-nums">{row.recovery_sessions ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer py-1">Drawdown definitions</summary>
        <p className="mt-2 leading-relaxed">
          {drawdowns.duration_definition} {drawdowns.recovery_definition}
        </p>
        {drawdowns.truncated && (
          <p className="mt-2">
            Showing the {drawdowns.shown} deepest of {drawdowns.total} recorded episodes.
          </p>
        )}
      </details>
    </div>
  )
}
