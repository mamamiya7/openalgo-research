import { Loader2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import type {
  PortfolioAnalysisStatus,
  PortfolioResult,
  StudyAnalysis,
} from '@/api/portfolioResearch'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { AnalysisCharts } from './AnalysisCharts'
import { AnalysisMetricTable } from './AnalysisMetricTable'

export interface AnalysisActions {
  busy: boolean
  response: PortfolioAnalysisStatus
  onPrepare: (parameters?: string[], symbol?: string) => void
  readOnly: boolean
  exportUrl: string
  freezeAnalysis?: boolean
}

export function AnalysisPreparation({
  busy,
  response,
  onPrepare,
  readOnly,
  missing,
}: AnalysisActions & { missing: boolean }) {
  if (busy) {
    return (
      <output className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        {response.status === 'running' ? 'Preparing analysis…' : 'Analysis queued…'}
      </output>
    )
  }
  if (response.status !== 'failed' && !missing) return null
  return (
    <div className="space-y-3 py-4">
      <output className="block text-sm text-muted-foreground">
        {response.error ??
          (readOnly
            ? 'Detailed analysis has not been saved for this result.'
            : 'Prepare a tear sheet from the saved results. No price download is needed.')}
      </output>
      {!readOnly && (
        <Button type="button" variant="outline" onClick={() => onPrepare()}>
          {response.status === 'failed' ? 'Retry analysis' : 'Prepare analysis'}
        </Button>
      )}
    </div>
  )
}

export function AnalysisBasis({ analysis }: { analysis: StudyAnalysis }) {
  if (!analysis.basis.length) return null
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer py-2">Calculation basis</summary>
      <ul className="ml-4 mt-2 list-disc space-y-2 leading-relaxed">
        {analysis.basis.map((text, index) => (
          <li key={`${index}-${text}`}>{text}</li>
        ))}
      </ul>
    </details>
  )
}

export function PortfolioTearSheet({
  result,
  ...actions
}: AnalysisActions & { result: PortfolioResult }) {
  const analysis = result.analysis
  const [priceSymbol, setPriceSymbol] = useState('')
  const symbols = analysis?.price_symbols ?? []
  const selectedSymbol = symbols.includes(priceSymbol)
    ? priceSymbol
    : (analysis?.price_symbol ?? symbols[0])
  return (
    <div className="min-w-0 space-y-6">
      <AnalysisPreparation {...actions} missing={!analysis} />
      {analysis && (
        <>
          <AnalysisCharts
            charts={analysis.charts}
            controls={(chart) =>
              chart.id === 'bars-with-fills' && symbols.length > 1 && !actions.readOnly ? (
                <div className="flex flex-wrap items-end gap-3">
                  <label className="space-y-2 text-xs text-muted-foreground">
                    <span className="block">Symbol</span>
                    <select
                      aria-label="Price chart symbol"
                      value={selectedSymbol}
                      onChange={(event) => setPriceSymbol(event.target.value)}
                      disabled={actions.busy}
                      className="h-9 rounded-md border bg-background px-3 text-sm text-foreground"
                    >
                      {symbols.map((symbol) => (
                        <option key={symbol} value={symbol}>
                          {symbol}
                        </option>
                      ))}
                    </select>
                  </label>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={actions.busy}
                    onClick={() => actions.onPrepare(undefined, selectedSymbol)}
                  >
                    Show prices
                  </Button>
                </div>
              ) : null
            }
          />
          <div className="flex items-center justify-between gap-3 border-t pt-3">
            <AnalysisBasis analysis={analysis} />
            {actions.response.status === 'complete' && (
              <Button asChild variant="ghost" size="sm">
                <a href={actions.exportUrl} download>
                  Export analysis
                </a>
              </Button>
            )}
          </div>
          <AnalysisMetricTable catalog={analysis.catalog} analysis={analysis} />
        </>
      )}
      {result.engine_records && <NativeRecords records={result.engine_records} />}
    </div>
  )
}

const axisLabels: Record<string, string> = {
  target_pct: 'Profit target',
  stop_pct: 'Stop loss',
  hold_sessions: 'Holding sessions',
  hold_minutes: 'Holding minutes',
  trailing_pct: 'Trailing stop',
  order_size_pct: 'Per-trade size',
  allocation_pct: 'Allocation',
}

export function PortfolioStudyAnalysis({
  result,
  ...actions
}: AnalysisActions & { result: PortfolioResult }) {
  const experiment = result.experiment
  const analysis = experiment?.study_analysis
  const parameters = Object.keys(experiment?.search_space?.axes ?? {})
  const [selection, setSelection] = useState<string[]>(() =>
    analysis?.parameters?.length ? analysis.parameters : parameters.slice(0, 2)
  )
  const selected = selection.filter((key) => parameters.includes(key))
  const parameterLabels = Object.fromEntries(
    parameters.map((key) => {
      const [id, parameter] = key.split('.')
      const name = result.strategies.find((strategy) => strategy.id === id)?.name ?? id
      const shortName = name.length > 28 ? `${name.slice(0, 27)}…` : name
      const label = axisLabels[parameter] ?? parameter
      return [key, result.strategies.length === 1 ? label : `${label} · ${shortName}`]
    })
  )
  return (
    <div className="min-w-0 space-y-6">
      <AnalysisPreparation {...actions} missing={!analysis} />
      {analysis && (
        <>
          {parameters.length > 1 && !actions.readOnly && (
            <div className="flex flex-wrap items-end gap-3">
              {[0, 1].map((index) => (
                <label key={index} className="min-w-0 space-y-2 text-xs text-muted-foreground">
                  <span className="block">Parameter {index + 1}</span>
                  <select
                    aria-label={`Study parameter ${index + 1}`}
                    value={selected[index] ?? ''}
                    disabled={actions.busy}
                    className="h-9 max-w-[min(100%,24rem)] rounded-md border bg-background px-3 text-sm text-foreground"
                    onChange={(event) => {
                      const next = [...selected]
                      next[index] = event.target.value
                      setSelection(next)
                    }}
                  >
                    {parameters.map((parameter) => (
                      <option
                        key={parameter}
                        value={parameter}
                        disabled={parameter === selected[1 - index]}
                      >
                        {parameterLabels[parameter]}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={actions.busy || selected.length !== 2 || selected[0] === selected[1]}
                onClick={() => actions.onPrepare(selected)}
              >
                Update charts
              </Button>
            </div>
          )}
          <AnalysisCharts charts={analysis.charts} parameterLabels={parameterLabels} />
          <div className="border-t pt-3">
            <AnalysisBasis analysis={analysis} />
          </div>
        </>
      )}
    </div>
  )
}

export function NativeRecords({
  records,
  expanded,
  onExpandedChange,
}: {
  records: NonNullable<PortfolioResult['engine_records']>
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
}) {
  const names = Object.keys(records).filter((key) => Array.isArray(records[key]))
  const [name, setName] = useState(names[0] ?? '')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const rows = useMemo(
    () =>
      (records[name] ?? []).filter(
        (row) =>
          !query.trim() || JSON.stringify(row).toLowerCase().includes(query.trim().toLowerCase())
      ),
    [records, name, query]
  )
  const keys = useMemo(() => [...new Set(rows.flatMap((row) => Object.keys(row)))], [rows])
  if (!names.length) return null
  return (
    <details className="border-t pt-3" open={expanded}>
      <summary
        className="cursor-pointer py-2 text-sm font-medium"
        onClick={
          onExpandedChange
            ? (event) => {
                event.preventDefault()
                onExpandedChange(!expanded)
              }
            : undefined
        }
      >
        Engine records
      </summary>
      <div className="mt-3 space-y-3">
        <div className="flex flex-wrap gap-3">
          <select
            aria-label="Engine record type"
            value={name}
            onChange={(event) => {
              setName(event.target.value)
              setPage(0)
            }}
            className="h-9 rounded-md border bg-background px-3 text-sm capitalize"
          >
            {names.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <Input
            aria-label="Find an engine record"
            placeholder="Find a record…"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setPage(0)
            }}
            className="max-w-xs"
          />
        </div>
        <div className="max-h-96 overflow-auto rounded-lg border">
          <table className="w-full text-left text-xs">
            <caption className="sr-only">Native engine {name}</caption>
            <thead className="sticky top-0 bg-muted">
              <tr>
                {keys.map((key) => (
                  <th scope="col" className="whitespace-nowrap p-3 font-medium" key={key}>
                    {key.replaceAll('_', ' ')}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.slice(page * 25, (page + 1) * 25).map((row, index) => (
                <tr key={`${name}-${page}-${index}`}>
                  {keys.map((key) => (
                    <td key={key} className="max-w-80 whitespace-nowrap p-3 tabular-nums">
                      {row[key] == null
                        ? '—'
                        : typeof row[key] === 'object'
                          ? JSON.stringify(row[key])
                          : String(row[key])}
                    </td>
                  ))}
                </tr>
              ))}
              {!rows.length && (
                <tr>
                  <td colSpan={Math.max(1, keys.length)} className="p-6 text-muted-foreground">
                    No records match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-end gap-3 text-xs text-muted-foreground">
          <span>
            {rows.length ? page * 25 + 1 : 0}–{Math.min((page + 1) * 25, rows.length)} of{' '}
            {rows.length}
          </span>
          {rows.length > 25 && (
            <>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!page}
                onClick={() => setPage(page - 1)}
              >
                Previous records
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={(page + 1) * 25 >= rows.length}
                onClick={() => setPage(page + 1)}
              >
                Next records
              </Button>
            </>
          )}
        </div>
      </div>
    </details>
  )
}
