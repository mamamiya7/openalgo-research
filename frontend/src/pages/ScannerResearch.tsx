import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect, useId, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import {
  type ExperimentKind,
  type ResearchConfig,
  type ResearchJob,
  type ResearchResult,
  type SearchSpec,
  type Submission,
  scannerResearch,
} from '@/api/scannerResearch'
import { PortfolioLineChart } from '@/components/portfolio/PortfolioLineChart'
import { ConnectorSettings, connectorIssue } from '@/components/research/ConnectorSettings'
import { ConfigSummary, PreflightReview, settingValue } from '@/components/research/PreflightReview'
import { ResearchSetup } from '@/components/research/ResearchSetup'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  type Draft,
  freshDraft,
  operationIdentity,
  readDraft,
  rememberSubmission,
  requestIdentity,
  specification,
  submissionHistory,
  updateSubmissionStatus,
  writeDraft,
} from '@/lib/researchDraft'
import { useAuthStore } from '@/stores/authStore'

const active = (status?: string) =>
  ['queued', 'running', 'cancelling', 'cancel_requested'].includes(status || '')
const names: Record<string, string> = {
  evaluated_all_passes: 'Distinct settings tested across the investigation',
  evaluated_this_pass: 'Settings tested in this pass',
  stored_rows: 'Recorded cumulative settings',
  stored_pass_rows: 'Recorded rows from this pass',
  grid: 'Settings in the search boundaries',
  remaining: 'Untested settings remaining',
  broad: 'Broad-stage evaluations',
  rank_by: 'Ranking objective',
  final_equity: 'Final equity',
  net_return_pct: 'Net return',
  max_drawdown_pct: 'Max drawdown',
  closed_trades: 'Closed trades',
  pending_trades: 'Pending',
  win_rate_pct: 'Win rate',
  sample_adequacy: 'Evidence strength',
  realized_equity: 'Realized-only equity (diagnostic)',
  order_size_pct: 'Allocation per signal',
  hold_sessions: 'Maximum holding sessions',
  pnl: 'Net profit / loss',
  fees: 'Costs',
}
const label = (text: string) => names[text] || text.replaceAll('_', ' ')
const currencyFields = new Set([
  'initial_capital',
  'final_equity',
  'equity',
  'cash',
  'realized_equity',
  'entry_price',
  'exit_price',
  'pnl',
  'fees',
  'entry_fee',
  'exit_fee',
  'mark_price',
  'mark_value',
  'available_exposure',
  'entry_raw_price',
  'exit_raw_price',
  'funded_budget',
  'requested_budget',
])
function tradeText(item: string, key: string): string {
  const terms: Record<string, string> = {
    trailing_stop: 'Trailing stop reached',
    stop: 'Stop loss reached',
    target: 'Profit target reached',
    hold: 'Maximum holding period reached',
    closed: 'Closed trade',
    pending: 'Pending outcome',
    skipped: 'Skipped signal',
    excluded: 'Excluded signal',
  }
  if (key === 'reason') {
    const [outcome, timing] = item.split('; ')
    if (terms[outcome]) {
      const timings: Record<string, string> = {
        'intraday exit': 'exit during the session',
        'open exit': 'exit at the session opening',
        'close exit': 'exit at the session close',
      }
      return `${terms[outcome]}${timing ? `; ${timings[timing] || timing}` : ''}`
    }
  }
  return ['status', 'outcome'].includes(key) ? terms[item] || item : settingValue(key, item)
}
const value = (item: unknown, key = ''): string =>
  item == null
    ? 'Unavailable'
    : typeof item === 'number'
      ? `${currencyFields.has(key) ? 'INR ' : ''}${item.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${key.endsWith('_pct') ? '%' : key.endsWith('_bps') ? ' bps' : ''}`
      : typeof item === 'object'
        ? Array.isArray(item) && item.every((entry) => typeof entry !== 'object')
          ? item.length
            ? settingValue(key, item)
            : 'None recorded'
          : evidenceExcerpt(item)
        : typeof item === 'boolean'
          ? item
            ? 'Yes'
            : 'No'
          : tradeText(String(item), key)
const money = (amount: number) =>
  `INR ${amount.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
/** Bounded previews never expand an arbitrary provenance tree into the DOM. */
function evidenceExcerpt(item: unknown): string {
  if (Array.isArray(item) && item.length === 0) return 'None recorded'
  let remaining = 60
  function project(input: unknown, depth: number): unknown {
    if (--remaining < 0) return '[Preview limit]'
    if (typeof input === 'string') return input.length > 500 ? `${input.slice(0, 500)}…` : input
    if (input === null || typeof input !== 'object') return input
    const count = Array.isArray(input) ? input.length : Object.keys(input).length
    if (depth >= 2) return `[${count} nested entries; exact content in export]`
    const entries = Array.isArray(input)
      ? input.slice(0, 8).map((entry, index) => [String(index), entry] as const)
      : Object.entries(input).slice(0, 8)
    const kept = entries.map(([key, entry]) => [key, project(entry, depth + 1)])
    if (count > 8) kept.push(['preview_note', `${count - 8} additional entries retained in export`])
    return Object.fromEntries(kept)
  }
  return JSON.stringify(project(item, 0), null, 2).slice(0, 3000)
}
function EvidenceDetails({ item, title }: { item: unknown; title: string }) {
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(0)
  const count = Array.isArray(item)
    ? item.length
    : item && typeof item === 'object'
      ? Object.keys(item).length
      : 1
  const entries: Array<readonly [string, unknown]> = open
    ? Array.isArray(item)
      ? item
          .slice(page * 10, (page + 1) * 10)
          .map((entry, index) => [String(page * 10 + index + 1), entry] as const)
      : item && typeof item === 'object'
        ? Object.entries(item).slice(page * 10, (page + 1) * 10)
        : [['Value', item]]
    : []
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer text-sm">
        {title} ({count} entries)
      </summary>
      {open && (
        <div className="mt-3 space-y-3 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-150">
          <p className="text-xs text-muted-foreground">
            Bounded preview: up to 10 entries per page with shortened nested values. Exact full
            evidence remains in the saved JSON export.
          </p>
          {entries.map(([key, entry]) => (
            <div key={key} className="space-y-1">
              <p className="break-all text-xs font-medium">{key}</p>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-2 text-xs">
                {evidenceExcerpt(entry)}
              </pre>
            </div>
          ))}
          {count > 10 && (
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                Previous evidence
              </Button>
              <span className="text-xs">
                {page * 10 + 1}–{Math.min((page + 1) * 10, count)} of {count}
              </span>
              <Button
                type="button"
                variant="outline"
                disabled={(page + 1) * 10 >= count}
                onClick={() => setPage(page + 1)}
              >
                Next evidence
              </Button>
            </div>
          )}
        </div>
      )}
    </details>
  )
}
function Evidence({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data)
  return (
    <div className="space-y-3">
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {entries.slice(0, 24).map(([key, item]) => (
          <div key={key} className="min-w-0">
            <dt className="text-xs capitalize text-muted-foreground">{label(key)}</dt>
            <dd className="break-words text-sm">
              {item !== null && typeof item === 'object' ? (
                Array.isArray(item) &&
                item.every((entry) => typeof entry === 'string' || typeof entry === 'number') ? (
                  <div>
                    {item.length
                      ? item
                          .slice(0, 8)
                          .map((entry) => String(entry).slice(0, 600))
                          .join('; ')
                      : 'None recorded'}
                    {item.length > 8 && (
                      <EvidenceDetails item={item} title={`Inspect all ${label(key)}`} />
                    )}
                  </div>
                ) : (
                  <EvidenceDetails item={item} title={`Inspect ${label(key)}`} />
                )
              ) : (
                value(item, key)
              )}
            </dd>
          </div>
        ))}
      </dl>
      {entries.length > 24 && (
        <EvidenceDetails
          item={Object.fromEntries(entries.slice(24))}
          title="Additional recorded fields"
        />
      )}
    </div>
  )
}
function EvidenceTable({
  rows,
  caption,
}: {
  rows: Array<Record<string, unknown>>
  caption: string
}) {
  const [page, setPage] = useState(0)
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState<{ key: string; descending: boolean } | null>(null)
  const filterId = useId()
  if (!rows.length) return <p className="text-sm text-muted-foreground">No rows recorded.</p>
  const allColumns = [...new Set(rows.flatMap((row) => Object.keys(row)))]
  const preferred = [
    'symbol',
    'signal_date',
    'status',
    'reason',
    'outcome',
    'entry_date',
    'exit_date',
    'entry_price',
    'exit_price',
    'quantity',
    'pnl',
    'fees',
    'date',
    'equity',
    'cash',
    'drawdown_pct',
    'open_positions',
  ]
  const columns = [
    ...preferred.filter((key) => allColumns.includes(key)),
    ...allColumns.filter((key) => !preferred.includes(key)),
  ]
  const visible = rows.filter((row) =>
    Object.values(row).some((item) => value(item).toLowerCase().includes(filter.toLowerCase()))
  )
  if (sort)
    visible.sort((a, b) => {
      const left = a[sort.key],
        right = b[sort.key]
      const order =
        typeof left === 'number' && typeof right === 'number'
          ? left - right
          : value(left).localeCompare(value(right))
      return sort.descending ? -order : order
    })
  const currentPage = Math.max(0, Math.min(page, Math.ceil(visible.length / 100) - 1))
  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Label htmlFor={filterId}>Filter {caption}</Label>
        <Input
          id={filterId}
          value={filter}
          placeholder="Match any recorded value"
          onChange={(event) => {
            setFilter(event.target.value)
            setPage(0)
          }}
        />
        <p className="text-xs text-muted-foreground">
          Showing {visible.length} matching rows of {rows.length} recorded rows. Sorting and filters
          affect this table only; exports and findings use all evidence.
        </p>
      </div>
      <section
        className="overflow-auto max-h-[32rem] rounded-md border"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: Scrollable evidence needs keyboard access.
        tabIndex={0}
        aria-label={caption}
      >
        <table className="w-full text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 bg-muted">
            <tr>
              {columns.map((key) => (
                <th
                  key={key}
                  scope="col"
                  aria-sort={
                    sort?.key === key ? (sort.descending ? 'descending' : 'ascending') : 'none'
                  }
                  className="whitespace-nowrap p-3 text-left capitalize"
                >
                  <button
                    type="button"
                    className="text-left capitalize underline-offset-4 hover:underline"
                    onClick={() =>
                      setSort({ key, descending: sort?.key === key ? !sort.descending : false })
                    }
                  >
                    {label(key)}
                    {sort?.key === key ? (sort.descending ? ' (descending)' : ' (ascending)') : ''}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.slice(currentPage * 100, (currentPage + 1) * 100).map((row, index) => (
              <tr
                key={String(row.signal_id ?? row.timestamp ?? row.date ?? index)}
                className="border-t"
              >
                {columns.map((key) => (
                  <td key={key} className="min-w-24 max-w-80 p-3 align-top break-words">
                    {value(row[key], key)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {visible.length > 100 && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Button
            variant="outline"
            disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}
          >
            Previous rows
          </Button>
          <span>
            Rows {currentPage * 100 + 1}–{Math.min((currentPage + 1) * 100, visible.length)} of{' '}
            {visible.length}
          </span>
          <Button
            variant="outline"
            disabled={(currentPage + 1) * 100 >= visible.length}
            onClick={() => setPage(currentPage + 1)}
          >
            Next rows
          </Button>
        </div>
      )}
    </div>
  )
}
function providerLabel(provenance: Record<string, unknown> = {}) {
  if (provenance.synthetic === true) return 'Demo · synthetic daily prices'
  if (provenance.history_source === 'historify')
    return `OpenAlgo prices · ${provenance.interval === '1m' ? '1 minute' : 'daily'}`
  const provider = String(provenance.provider || '')
  const nativeBroker = provider.match(/^(.+?) via OpenAlgo history/i)?.[1]
  if (nativeBroker) return `${nativeBroker} · daily`
  if (/^[a-z][a-z0-9_]*$/.test(provider)) {
    return `${provider.charAt(0).toUpperCase()}${provider.slice(1)} · daily`
  }
  if (/nse|bhavcopy/i.test(provider)) return 'NSE archive · daily'
  return provider ? `${provider} · daily` : 'Price source not recorded'
}
function TradeTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<{ key: string; descending: boolean }>({
    key: 'signal_date',
    descending: false,
  })
  const filtered = rows
    .filter((row) =>
      [row.symbol, row.outcome, row.status, row.reason].some((item) =>
        String(item || '')
          .toLowerCase()
          .includes(query.toLowerCase())
      )
    )
    .sort(
      (a, b) =>
        (typeof a[sort.key] === 'number' && typeof b[sort.key] === 'number'
          ? Number(a[sort.key]) - Number(b[sort.key])
          : String(a[sort.key] || '').localeCompare(String(b[sort.key] || ''))) *
        (sort.descending ? -1 : 1)
    )
  const current = Math.min(page, Math.max(0, Math.ceil(filtered.length / 25) - 1))
  const outcome = (row: Record<string, unknown>) => {
    if (row.status === 'pending')
      return /gap|missing/i.test(String(row.reason)) ? 'Pending · price gap' : 'Pending'
    return (
      (
        {
          trailing_stop: 'Trailing stop',
          hold: 'Time exit',
          target: 'Profit target',
          stop: 'Stop loss',
          skipped: 'Skipped',
          excluded: 'Excluded',
        } as Record<string, string>
      )[String(row.outcome || row.status)] || 'Closed'
    )
  }
  return (
    <div className="space-y-4">
      <Input
        aria-label="Search trades"
        placeholder="Search symbol or outcome"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value)
          setPage(0)
        }}
        className="max-w-sm"
      />
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users need to scroll wide trade tables. */}
      <section aria-label="Trades table" tabIndex={0} className="overflow-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              {[
                ['symbol', 'Symbol'],
                ['entry_date', 'Entry'],
                ['exit_date', 'Exit'],
                ['quantity', 'Qty'],
                ['pnl', 'P&L'],
                ['outcome', 'Outcome'],
              ].map(([key, title]) => (
                <th
                  key={key}
                  scope="col"
                  className="p-3 text-left"
                  aria-sort={
                    sort.key === key ? (sort.descending ? 'descending' : 'ascending') : 'none'
                  }
                >
                  <button
                    type="button"
                    onClick={() =>
                      setSort({ key, descending: sort.key === key && !sort.descending })
                    }
                  >
                    {title}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.slice(current * 25, current * 25 + 25).map((row, index) => (
              <tr key={String(row.signal_id || index)} className="border-t">
                <td className="p-3 align-top font-medium">
                  <details>
                    <summary className="cursor-pointer">{String(row.symbol)}</summary>
                    <dl className="mt-2 min-w-44 space-y-1 text-xs font-normal">
                      {[
                        'signal_date',
                        'entry_raw_price',
                        'exit_raw_price',
                        'fees',
                        'funded_budget',
                        'requested_budget',
                        'available_exposure',
                        'reason',
                      ]
                        .filter((key) => row[key] != null)
                        .map((key) => (
                          <div key={key}>
                            <dt className="text-muted-foreground">{label(key)}</dt>
                            <dd>{value(row[key], key)}</dd>
                          </div>
                        ))}
                    </dl>
                  </details>
                </td>
                <td className="whitespace-nowrap p-3 align-top">
                  {String(row.entry_date || '—')}
                  {row.entry_timestamp != null && (
                    <span className="block text-xs">
                      {String(row.entry_timestamp).slice(11, 16)} IST
                    </span>
                  )}
                  <span className="block text-xs text-muted-foreground">
                    {row.entry_price == null ? '' : money(Number(row.entry_price))}
                  </span>
                </td>
                <td className="whitespace-nowrap p-3 align-top">
                  {String(row.exit_date || '—')}
                  {row.exit_timestamp != null && (
                    <span className="block text-xs">
                      {String(row.exit_timestamp).slice(11, 16)} IST
                    </span>
                  )}
                  <span className="block text-xs text-muted-foreground">
                    {row.exit_price == null ? '' : money(Number(row.exit_price))}
                  </span>
                </td>
                <td className="p-3 align-top">{String(row.quantity ?? '—')}</td>
                <td className="whitespace-nowrap p-3 align-top">
                  {row.pnl == null ? '—' : money(Number(row.pnl))}
                </td>
                <td className="whitespace-nowrap p-3 align-top">{outcome(row)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <div className="flex items-center justify-between gap-2 text-sm">
        <Button variant="ghost" disabled={current === 0} onClick={() => setPage(current - 1)}>
          Previous
        </Button>
        <span>
          {filtered.length ? current * 25 + 1 : 0}–{Math.min((current + 1) * 25, filtered.length)}{' '}
          of {filtered.length}
        </span>
        <Button
          variant="ghost"
          disabled={(current + 1) * 25 >= filtered.length}
          onClick={() => setPage(current + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  )
}
function Report({
  result,
  window,
  beforeLedger,
}: {
  result: ResearchResult
  window?: Record<string, unknown>
  beforeLedger?: ReactNode
}) {
  const series = useMemo(
    () => [
      {
        name: result.metric_basis === 'minute_marked' ? 'Portfolio value' : 'Daily marked equity',
        color: '#0d9488',
        area: true,
        data: result.equity_curve.map((row) => ({
          date: String(row.timestamp || row.date),
          value: Number(row.equity),
        })),
      },
    ],
    [result.equity_curve, result.metric_basis]
  )
  const initialRange = useMemo(() => {
    // Focus a completed minute run on its trades without trimming saved marks.
    // Later-period reports and unresolved positions retain their full horizon.
    if (
      window ||
      result.metric_basis !== 'minute_marked' ||
      result.equity_curve.length < 2 ||
      result.ledger.some((row) => row.status === 'pending')
    )
      return undefined
    const trades = result.ledger.filter(
      (row) => Number(row.quantity) > 0 && row.entry_timestamp && row.exit_timestamp
    )
    if (!trades.length) return undefined
    const first = trades.reduce(
      (earliest, row) =>
        String(row.entry_timestamp) < earliest ? String(row.entry_timestamp) : earliest,
      String(trades[0].entry_timestamp)
    )
    const last = trades.reduce(
      (latest, row) => (String(row.exit_timestamp) > latest ? String(row.exit_timestamp) : latest),
      String(trades[0].exit_timestamp)
    )
    const start = result.equity_curve.findIndex((row) => String(row.timestamp) >= first)
    const end = result.equity_curve.findIndex((row) => String(row.timestamp) >= last)
    if (start < 0) return undefined
    return {
      from: Math.max(0, start - 1),
      to: Math.max(1, end < 0 ? result.equity_curve.length - 1 : end),
    }
  }, [result.equity_curve, result.ledger, result.metric_basis, window])
  const provenance = (result.coverage.provenance || {}) as Record<string, unknown>
  const warmup = window
    ? result.equity_curve.filter((row) => String(row.date) < String(window.test_from)).length
    : 0
  return (
    <div className="space-y-5">
      <p className="text-sm font-medium">{providerLabel(provenance)}</p>
      <Tabs defaultValue="summary" className="w-full">
        <TabsList>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="trades">Trades</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="summary" className="space-y-6 pt-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3 lg:grid-cols-6">
            {[
              'final_equity',
              'net_return_pct',
              'max_drawdown_pct',
              'closed_trades',
              'pending_trades',
              'win_rate_pct',
            ]
              .filter((key) => key in result.summary)
              .map((key) => (
                <div key={key}>
                  <dt className="text-xs text-muted-foreground">{label(key)}</dt>
                  <dd className="mt-1 text-xl font-semibold tabular-nums">
                    {value(
                      key === 'pending_trades'
                        ? Number(result.summary.pending_trades || 0) +
                            Number(result.summary.unfunded_pending || 0)
                        : result.summary[key],
                      key
                    )}
                  </dd>
                </div>
              ))}
          </dl>
          {/insufficient|small|few|limited/i.test(String(result.summary.sample_adequacy || '')) && (
            <p className="text-xs text-muted-foreground">
              {String(result.summary.sample_adequacy)}
            </p>
          )}
          {window && (
            <p className="text-xs text-muted-foreground">
              {String(window.test_from)} – {String(window.test_end)} ·{' '}
              {String(window.test_sessions)} sessions
              {warmup ? ` · chart includes ${warmup} earlier warmup sessions` : ''}
            </p>
          )}
          {series[0].data.length ? (
            <PortfolioLineChart series={series} format={money} initialRange={initialRange} />
          ) : (
            <p className="text-sm text-muted-foreground">No equity curve available.</p>
          )}
          <div className="flex flex-wrap justify-between gap-3 text-xs text-muted-foreground">
            <span>Portfolio value includes open positions.</span>
            <details>
              <summary className="cursor-pointer">View portfolio values</summary>
              <EvidenceTable rows={result.equity_curve} caption="Portfolio values" />
            </details>
          </div>
          {beforeLedger && (
            <details>
              <summary className="cursor-pointer text-sm font-medium">Compare settings</summary>
              <div className="mt-4 space-y-4">{beforeLedger}</div>
            </details>
          )}
        </TabsContent>
        <TabsContent value="trades" className="pt-4">
          <TradeTable rows={result.ledger} />
        </TabsContent>
        <TabsContent value="settings" className="pt-4">
          {result.execution && (
            <div className="mb-4 space-y-2 text-sm">
              <p>
                {result.execution.engine === 'vectorbt' ? 'VectorBT' : 'Scanner'}
                {result.execution.optimizer === 'optuna' ? ' · Optuna' : ''}
              </p>
              {result.execution.engine === 'vectorbt' && (
                <p className="text-muted-foreground">
                  Protective exits start on the following session.
                </p>
              )}
            </div>
          )}
          <ConfigSummary config={result.config} hideInactive />
        </TabsContent>
      </Tabs>
    </div>
  )
}
function ExperimentReport({
  result,
  onTransfer,
  onFollowUp,
}: {
  result: ResearchResult
  onTransfer: (config: ResearchConfig, kind: ExperimentKind) => void
  onFollowUp: (spec: Record<string, unknown>) => void
}) {
  const experiment = result.experiment
  const [choice, setChoice] = useState(experiment?.recommendation_id || '')
  const [foldIndex, setFoldIndex] = useState(0)
  if (!experiment) return <Report result={result} />
  const selected =
    experiment.selected_reports?.[choice] ||
    (choice === experiment.recommendation_id ? result : null)
  const candidates = experiment.rows || []
  const selectedConfig =
    selected?.config || candidates.find((row) => row.config_id === choice)?.config
  const selectable = [experiment.recommendation_id, ...(experiment.alternatives || [])].filter(
    (id): id is string => !!id
  )
  function candidateLabel(id: string, index: number) {
    const report = experiment?.selected_reports?.[id],
      summary = report?.summary || candidates.find((row) => row.config_id === id)?.summary
    const baseline =
      experiment?.selected_reports?.[experiment?.recommendation_id || '']?.summary || result.summary
    const purpose =
      index === 0
        ? 'Recorded recommendation'
        : Number(summary?.max_drawdown_pct) < Number(baseline.max_drawdown_pct)
          ? 'Lower drawdown option'
          : Number(summary?.win_rate_pct) > Number(baseline.win_rate_pct)
            ? 'Higher completed-trade win rate'
            : 'Different risk / return balance'
    return `${purpose} · return ${summary?.net_return_pct == null ? 'unavailable' : `${Number(summary.net_return_pct).toFixed(2)}%`} · drawdown ${summary?.max_drawdown_pct == null ? 'unavailable' : `${Number(summary.max_drawdown_pct).toFixed(2)}%`}${report ? ` · target ${report.config.target_pct}% / stop ${report.config.stop_pct}%` : ''}`
  }
  const verdicts: Record<string, string> = {
    final_insufficient_evidence: 'Too few independent completed observations to establish an edge.',
    final_no_reliable_edge: 'The tested settings have not demonstrated a reliable historical edge.',
    final_useful_result: 'A historical candidate is available for a later-period check.',
  }
  const comparisons = (
    <>
      {experiment.neighborhoods && (
        <Card>
          <CardHeader>
            <CardTitle>Tested neighborhood evidence</CardTitle>
          </CardHeader>
          <CardContent>
            <EvidenceTable
              rows={experiment.neighborhoods}
              caption="Neighborhood statistics across all evaluated rows"
            />
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader>
          <CardTitle>All recorded candidates</CardTitle>
        </CardHeader>
        <CardContent>
          <EvidenceTable
            rows={candidates.map((row) => ({
              target_pct: row.config.target_pct,
              stop_pct: row.config.stop_pct,
              hold_sessions: row.config.hold_sessions,
              modes: row.config.modes,
              trailing_enabled: row.config.trailing_enabled,
              trailing_pct: row.config.trailing_pct,
              net_return_pct: row.summary.net_return_pct,
              max_drawdown_pct: row.summary.max_drawdown_pct,
              closed_trades: row.summary.closed_trades,
              score: row.score,
            }))}
            caption="Search candidates; filters change display only"
          />
          <EvidenceDetails
            item={candidates}
            title="Exact candidate identities and all recorded fields"
          />
          {experiment.pass_rows && (
            <EvidenceDetails
              item={experiment.pass_rows}
              title="New evaluations from this pass only"
            />
          )}
        </CardContent>
      </Card>
    </>
  )
  return (
    <div className="space-y-6">
      {experiment.kind !== 'research' && experiment.state && (
        <p className="text-sm text-muted-foreground">
          {verdicts[experiment.state] || experiment.state.replaceAll('_', ' ')}
        </p>
      )}
      {experiment.kind === 'optimize' && (
        <>
          <div className="space-y-3">
            <Label htmlFor="candidate-choice">Displayed exact setting</Label>
            <select
              id="candidate-choice"
              className="w-full min-w-0 rounded-md border bg-background p-2 text-sm"
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
            >
              {selectable.map((id, index) => (
                <option key={id} value={id}>
                  {candidateLabel(id, index)}
                </option>
              ))}
            </select>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={!selectedConfig}
                onClick={() => selectedConfig && onTransfer(selectedConfig, 'backtest')}
              >
                Backtest this exact setting
              </Button>
              {result.execution?.engine !== 'vectorbt' && (
                <Button
                  variant="outline"
                  disabled={!selectedConfig}
                  onClick={() => selectedConfig && onTransfer(selectedConfig, 'research')}
                >
                  Test this exact setting later
                </Button>
              )}
              {result.execution?.engine !== 'vectorbt' && (
                <Button
                  variant="outline"
                  disabled={!selectedConfig}
                  onClick={() => selectedConfig && onTransfer(selectedConfig, 'sensitivity')}
                >
                  Check this setting's sensitivity
                </Button>
              )}
            </div>
          </div>
          {selected ? (
            <Report result={selected} beforeLedger={comparisons} />
          ) : (
            <p className="text-sm">
              The report for this selected setting was not retained. Its curve and ledger are
              unavailable.
            </p>
          )}
          {experiment.follow_up && (
            <Card>
              <CardHeader>
                <CardTitle>A distinct follow-up is available</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm">
                  This pass is complete. A new job can investigate previously untested settings
                  within these boundaries; it does not resume or change this saved result.
                </p>
                <Button
                  variant="outline"
                  onClick={() => onFollowUp(experiment.follow_up as Record<string, unknown>)}
                >
                  Prepare new follow-up
                </Button>
              </CardContent>
            </Card>
          )}
        </>
      )}
      {experiment.kind === 'research' && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="space-y-2">
              <Label htmlFor="later-window">Later period</Label>
              <select
                id="later-window"
                className="block max-w-full rounded-md border bg-background p-2 text-sm"
                value={foldIndex}
                onChange={(e) => setFoldIndex(Number(e.target.value))}
              >
                {experiment.folds?.map((fold, index) => (
                  <option key={index} value={index}>
                    Window {index + 1} · {String(fold.window.test_from)} –{' '}
                    {String(fold.window.test_end)}
                  </option>
                ))}
              </select>
            </div>
            <details className="max-w-md text-xs text-muted-foreground">
              <summary className="cursor-pointer">
                {experiment.exploration?.prior_explored ||
                experiment.exploration?.known_overlap_jobs.length
                  ? 'Previously explored data'
                  : 'Exploration history'}
              </summary>
              <p className="mt-2">
                {experiment.exploration?.interpretation || 'Exploration history not recorded.'}{' '}
                {experiment.exploration
                  ? `${experiment.exploration.known_overlap_jobs.length} overlapping saved runs.`
                  : ''}{' '}
                Each window starts with fresh capital.
              </p>
            </details>
          </div>
          {experiment.folds
            ?.filter((_, index) => index === foldIndex)
            .map((fold) => (
              <div key={foldIndex} className="space-y-4">
                {fold.finding && fold.finding !== fold.test_report?.summary.sample_adequacy && (
                  <p className="text-sm text-muted-foreground">{fold.finding}</p>
                )}
                {fold.test_report && (
                  <Report
                    result={fold.test_report}
                    window={fold.window}
                    beforeLedger={
                      <>
                        {fold.training_ranks.length > 0 && (
                          <details>
                            <summary className="cursor-pointer text-sm">
                              Earlier-period selection
                            </summary>
                            <EvidenceTable
                              rows={fold.training_ranks.map((row) => ({
                                ...row.config,
                                ...row.summary,
                                score: row.score,
                              }))}
                              caption="Earlier-period settings"
                            />
                          </details>
                        )}
                        {fold.sensitivities.map((variant) => (
                          <details key={variant.name}>
                            <summary className="cursor-pointer text-sm">
                              {variant.name} · {variant.return_change_pp.toFixed(2)} pp return
                              change
                            </summary>
                            <Report result={variant.report} window={fold.window} />
                          </details>
                        ))}
                      </>
                    }
                  />
                )}
              </div>
            ))}
        </div>
      )}
      {experiment.kind === 'sensitivity' && (
        <>
          <h3 className="text-xl font-semibold">Fixed baseline</h3>
          <Report result={result} />
          {experiment.variants?.map((variant) => (
            <details key={variant.name}>
              <summary className="cursor-pointer text-sm font-medium">
                {variant.name} · Return change {variant.return_change_pp.toFixed(2)} percentage
                points
              </summary>
              <Evidence data={variant.changes as Record<string, unknown>} />
              <Report result={variant.report} />
            </details>
          ))}
        </>
      )}
    </div>
  )
}
export default function ScannerResearch() {
  const owner = useAuthStore((state) => state.user?.username) || 'account'
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const selectedId = params.get('job') || ''
  const [draft, setDraft] = useState<Draft>(() => readDraft(owner))
  const [file, setFile] = useState<File | null>(null)
  const [sourceKind, setSourceKind] = useState('broker')
  const [savedOpen, setSavedOpen] = useState(false)
  const [preparationId, setPreparationId] = useState(() => {
    try {
      return sessionStorage.getItem(`research-preparation:${owner}`) || ''
    } catch {
      return ''
    }
  })
  useEffect(() => {
    try {
      if (preparationId) sessionStorage.setItem(`research-preparation:${owner}`, preparationId)
      else sessionStorage.removeItem(`research-preparation:${owner}`)
    } catch {
      /* Saved job list remains the recovery path. */
    }
  }, [owner, preparationId])
  const [preflightSignature, setPreflightSignature] = useState('')
  const [continueReview, setContinueReview] = useState(false)
  const [requestError, setRequestError] = useState('')
  const [, setIntakeNotice] = useState('')
  const [savedFilter, setSavedFilter] = useState({ q: '', kind: '', status: '' })
  const [savedCursor, setSavedCursor] = useState('')
  const [cursorHistory, setCursorHistory] = useState<string[]>([])
  const [updateEnd, setUpdateEnd] = useState('')
  const health = useQuery({
    queryKey: ['research-health'],
    queryFn: scannerResearch.health,
    refetchInterval: 10000,
  })
  const capabilities = useQuery({
    queryKey: ['research-capabilities'],
    queryFn: scannerResearch.capabilities,
  })
  useEffect(() => writeDraft(owner, draft), [owner, draft])
  const sources = useQuery({
    queryKey: ['research-sources', owner],
    queryFn: scannerResearch.sources,
  })
  const jobs = useQuery({
    queryKey: ['research-jobs', savedFilter, savedCursor],
    queryFn: () => scannerResearch.jobs({ page_size: 30, cursor: savedCursor, ...savedFilter }),
    refetchInterval: (query) =>
      query.state.data?.items.some((row) => active(row.status)) ? 2000 : false,
  })
  const job = useQuery({
    queryKey: ['research-job', selectedId],
    queryFn: () => scannerResearch.job(selectedId),
    enabled: !!selectedId,
    refetchInterval: (query) => (active(query.state.data?.status) ? 1000 : false),
  })
  const preparation = useQuery({
    queryKey: ['research-preparation', preparationId],
    queryFn: () => scannerResearch.job(preparationId),
    enabled: !!preparationId,
    refetchInterval: (query) => (active(query.state.data?.status) ? 1500 : false),
  })
  useEffect(() => {
    const prepared = preparation.data?.result?.prepared_source
    if (preparationId && prepared) {
      setDraft((current) => ({ ...current, source: prepared }))
      setPreparationId('')
      queryClient.invalidateQueries({ queryKey: ['research-sources', owner] })
    }
  }, [preparation.data, preparationId, queryClient, owner])
  const payload = useMemo(
    () => ({
      source_id: draft.source?.id || '',
      config: draft.config,
      kind: draft.kind,
      specification: specification(draft),
    }),
    [draft]
  )
  const signature = JSON.stringify(payload)
  const preflight = useMutation({
    mutationFn: (submitted: Submission) => scannerResearch.preflight(submitted),
    onSuccess: (data, submitted) => {
      if (data.preparation_job) {
        setPreflightSignature('')
        setContinueReview(true)
        setPreparationId((data.preparation_job as ResearchJob).id)
      } else {
        setContinueReview(false)
        setPreflightSignature(JSON.stringify(submitted))
      }
    },
    onError: () => setContinueReview(false),
  })
  const reviewedSpec = preflight.data?.specification as Record<string, unknown> | undefined
  const submissionPayload = useMemo(
    () =>
      reviewedSpec?.execution && preflightSignature === signature
        ? { ...payload, specification: reviewedSpec }
        : payload,
    [reviewedSpec, preflightSignature, signature, payload]
  )
  const priorSubmission = submissionHistory(owner, submissionPayload)
  const terminalRetry =
    priorSubmission && ['failed', 'cancelled', 'interrupted'].includes(priorSubmission.status)
      ? priorSubmission
      : null
  useEffect(() => {
    if (job.data) updateSubmissionStatus(owner, submissionPayload, job.data.id, job.data.status)
  }, [owner, submissionPayload, job.data])
  useEffect(() => {
    if (continueReview && !preparationId && payload.source_id && !preflight.isPending) {
      setContinueReview(false)
      preflight.mutate(payload)
    }
  }, [continueReview, preparationId, payload, preflight.isPending, preflight.mutate])
  const refreshJobs = () => {
    queryClient.invalidateQueries({ queryKey: ['research-jobs'] })
    queryClient.invalidateQueries({ queryKey: ['research-job', selectedId] })
  }
  const upload = useMutation({
    mutationFn: () =>
      scannerResearch.upload(file as File, sourceKind, {
        config: draft.config,
        kind: draft.kind,
        specification: specification(draft),
      }),
    onSuccess: (received) => {
      if ('preparation_job' in received) {
        setIntakeNotice(
          received.preparation_job.status === 'completed'
            ? 'This CSV already has a successful preparation. Its frozen source is being reused; no new prices were downloaded.'
            : 'Source preparation is recorded as a background job. Closing this page does not cancel it.'
        )
        setDraft((current) => ({ ...current, source: null }))
        setPreparationId(received.preparation_job.id)
      } else {
        setDraft((current) => ({ ...current, source: received }))
        queryClient.invalidateQueries({ queryKey: ['research-sources', owner] })
      }
    },
  })
  const submit = useMutation({
    mutationFn: (submitted: Submission) => {
      const history = submissionHistory(owner, submitted)
      return history && ['failed', 'cancelled', 'interrupted'].includes(history.status)
        ? scannerResearch.retry(
            history.job_id,
            operationIdentity(owner, 'retry', { previous_attempt_id: history.job_id })
          )
        : scannerResearch.submit(requestIdentity(owner, submitted))
    },
    onSuccess: (created, submitted) => {
      rememberSubmission(owner, submitted, created.id, created.status)
      setParams({ job: created.id })
      refreshJobs()
    },
  })
  const cancel = useMutation({
    mutationFn: () => scannerResearch.cancel(selectedId),
    onSuccess: refreshJobs,
  })
  const resume = useMutation({
    mutationFn: () => scannerResearch.resume(selectedId),
    onSuccess: refreshJobs,
  })
  const retry = useMutation({
    mutationFn: (id: string) =>
      scannerResearch.retry(id, operationIdentity(owner, 'retry', { previous_attempt_id: id })),
    onSuccess: (created) => {
      setParams({ job: created.id })
      if (['prepare_source', 'acquire'].includes(created.kind || '')) setPreparationId(created.id)
      refreshJobs()
    },
  })
  const evidenceUpdate = useMutation({
    mutationFn: () => {
      const sourceId = draft.source?.id || preparation.data?.source_id || job.data?.source_id || ''
      return scannerResearch.updateEvidence(
        sourceId,
        updateEnd,
        operationIdentity(owner, 'evidence_update', { source_id: sourceId, end_date: updateEnd })
      )
    },
    onSuccess: (created) => {
      setParams({ job: created.id })
      refreshJobs()
    },
  })
  useEffect(() => {
    if (job.data?.kind === 'evidence_update' && job.data.status === 'completed')
      queryClient.invalidateQueries({ queryKey: ['research-capabilities'] })
  }, [job.data?.kind, job.data?.status, queryClient])
  const source = draft.source
  const engineIssue = connectorIssue(draft, capabilities.data?.connectors)
  async function transfer(
    config: ResearchConfig,
    kind: ExperimentKind,
    spec?: Record<string, unknown>
  ) {
    if (!job.data) return
    let originalSource = sources.data?.find((item) => item.id === job.data?.source_id)
    try {
      if (!originalSource) originalSource = await scannerResearch.source(job.data.source_id)
    } catch (error) {
      setRequestError(
        error instanceof Error ? error.message : 'Could not reopen the original frozen source.'
      )
      return
    }
    setDraft((current) => ({
      ...current,
      source: originalSource,
      config: structuredClone(config),
      kind,
      execution: job.data?.result?.execution,
      research: { ...current.research, intent: 'fixed_setup', prior_explored: true },
      ...(spec ? { search: spec as unknown as SearchSpec } : {}),
    }))
    setParams({})
    setRequestError('')
  }
  const errors = [
    upload.error,
    submit.error,
    preflight.error,
    cancel.error,
    resume.error,
    retry.error,
    evidenceUpdate.error,
    sources.error,
    jobs.error,
    job.error,
    preparation.error,
  ].filter(Boolean)
  const needsWorker = !selectedId || (job.data && job.data.status !== 'completed')
  return (
    <div className="min-w-0 space-y-6 [&_button]:motion-safe:transition-colors [&_button]:motion-safe:duration-150 [&_button]:motion-reduce:transition-none [&_summary]:motion-safe:transition-colors [&_summary]:motion-safe:duration-150 [&_summary]:hover:text-primary">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Scanner Research</h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setSavedOpen(true)}>
            Saved runs
          </Button>
          <Button
            variant="outline"
            disabled={upload.isPending || preflight.isPending || submit.isPending}
            onClick={() => {
              setDraft(freshDraft())
              setFile(null)
              setSourceKind('broker')
              setPreparationId('')
              setPreflightSignature('')
              setContinueReview(false)
              setRequestError('')
              upload.reset()
              preflight.reset()
              submit.reset()
              setParams({})
            }}
          >
            New run
          </Button>
        </div>
      </header>
      {needsWorker &&
        (health.isError || (health.data && health.data.worker_state !== 'online')) && (
          <div className="rounded-md border p-3 text-sm" aria-live="polite">
            {health.isError
              ? 'Worker availability could not be checked. Refresh status before relying on queue progress.'
              : health.data?.worker_state === 'maintenance'
                ? 'Storage maintenance is active. New work is paused; wait for maintenance to finish and refresh status.'
                : health.data?.worker_state === 'stale'
                  ? 'Worker heartbeat is stale. Existing evidence is safe; ask the instance operator to restart the research worker before retrying interrupted work.'
                  : health.data?.worker_state === 'offline'
                    ? 'Calculation worker is offline. Saved evidence remains available; ask the instance operator to start the research worker. Queued jobs will wait.'
                    : 'Checking calculation worker…'}
            <Button variant="ghost" onClick={() => health.refetch()}>
              Refresh worker status
            </Button>
          </div>
        )}
      {errors.map((error, index) => (
        <p
          key={`${error?.message}-${index}`}
          role="alert"
          className="break-words rounded-md border border-destructive p-3 text-sm text-destructive"
        >
          {error?.message}
        </p>
      ))}
      {requestError && (
        <p role="alert" className="text-sm text-destructive">
          {requestError}
        </p>
      )}
      {selectedId ? (
        <section aria-label="Selected research run" className="space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="flex flex-wrap gap-x-1 text-xl font-semibold">
                {(job.data?.title || `Saved run ${selectedId.slice(0, 8)}`)
                  .split(' · ')
                  .map((part, index, parts) => (
                    <span
                      key={index}
                      className={
                        /^\d{4}-\d{2}-\d{2}$/.test(part)
                          ? 'whitespace-nowrap'
                          : 'min-w-0 break-words [overflow-wrap:anywhere]'
                      }
                    >
                      {part}
                      {index < parts.length - 1 ? ' ·' : ''}
                    </span>
                  ))}
              </h2>
            </div>
            {job.data?.result && (
              <Button asChild variant="outline" className="h-auto whitespace-normal">
                <a
                  href={scannerResearch.exportUrl(selectedId)}
                  download
                  title="Download all exact saved evidence as JSON"
                >
                  Export results
                </a>
              </Button>
            )}
          </div>
          {job.isPending && <output>Opening saved evidence…</output>}
          {job.data && (
            <>
              {job.data.status !== 'completed' && (
                <output className="block text-sm">
                  Status: {label(job.data.status)}
                  {active(job.data.status) ? ` · ${job.data.progress}%` : ''}
                  {active(job.data.status) && job.data.queue_position != null
                    ? ` · Queue position ${job.data.queue_position}`
                    : ''}
                </output>
              )}
              {active(job.data.status) && (
                <>
                  <progress
                    aria-label="Calculation progress"
                    max={100}
                    value={job.data.progress}
                    className="w-full"
                  />
                  <p className="text-xs text-muted-foreground">
                    You can leave this page and return to check progress.
                  </p>
                  <Button
                    variant="outline"
                    disabled={cancel.isPending || job.data.status === 'cancel_requested'}
                    onClick={() => cancel.mutate()}
                  >
                    Cancel run
                  </Button>
                </>
              )}
              {job.data.error && (
                <p role="alert" className="text-sm text-destructive">
                  {job.data.error}
                </p>
              )}
              {job.data.resumable && !active(job.data.status) && (
                <div className="space-y-2">
                  <p className="text-sm">Continue this run from its saved progress.</p>
                  <Button disabled={resume.isPending} onClick={() => resume.mutate()}>
                    Resume run
                  </Button>
                </div>
              )}
              {!job.data.resumable &&
                ['failed', 'cancelled', 'interrupted'].includes(job.data.status) && (
                  <div className="space-y-2">
                    <p className="text-sm">
                      After resolving the issue, retry with the same signals and settings. This run
                      stays saved.
                    </p>
                    <Button
                      disabled={retry.isPending || health.data?.maintenance}
                      onClick={() => retry.mutate(selectedId)}
                    >
                      Retry run
                    </Button>
                  </div>
                )}
              {job.data.kind === 'evidence_update' && job.data.status === 'completed' ? (
                <div className="space-y-3">
                  <h3 className="font-semibold">Official evidence update completed</h3>
                  <p className="text-sm">
                    Retry source preparation to use the updated prices. Existing sources stay
                    unchanged.
                  </p>
                  <Button
                    onClick={() => {
                      capabilities.refetch()
                      setParams({})
                    }}
                  >
                    Return to source preparation
                  </Button>
                </div>
              ) : job.data.result?.prepared_source ? (
                <div className="space-y-4">
                  <h3 className="text-lg font-semibold">Prices are ready</h3>
                  <p className="text-sm text-muted-foreground">
                    {job.data.result.prepared_source.receipt.signal_count} signals ·{' '}
                    {providerLabel(job.data.result.prepared_source.provenance)}
                  </p>
                  <Button
                    onClick={() => {
                      setDraft((current) => ({
                        ...current,
                        source: job.data?.result?.prepared_source || null,
                      }))
                      setParams({})
                    }}
                  >
                    Use these signals
                  </Button>
                </div>
              ) : (
                job.data.result && (
                  <div className="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-200">
                    <ExperimentReport
                      key={selectedId}
                      result={job.data.result}
                      onTransfer={transfer}
                      onFollowUp={(spec) =>
                        transfer(job.data?.config as ResearchConfig, 'optimize', spec)
                      }
                    />
                  </div>
                )
              )}
            </>
          )}
        </section>
      ) : (
        <div className="grid min-w-0 gap-6 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-200 xl:grid-cols-2">
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>1. Upload signals</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2 text-sm">
                {capabilities.data ? (
                  <>
                    <p className="font-medium">
                      {source
                        ? providerLabel(source.provenance)
                        : sourceKind === 'broker'
                          ? 'OpenAlgo prices · automatic'
                          : sourceKind === 'fixture'
                            ? 'Demo · synthetic daily prices'
                            : sourceKind === 'public'
                              ? 'NSE archive · daily'
                              : 'OpenAlgo stored prices'}
                    </p>
                    {!source &&
                      sourceKind === 'broker' &&
                      !capabilities.data.history?.can_prepare &&
                      capabilities.data.broker.state !== 'ready_to_download' && (
                        <p className="text-muted-foreground">
                          {capabilities.data.broker.message ||
                            'Connect your broker in OpenAlgo to download missing prices.'}{' '}
                          {capabilities.data.broker.action_url && (
                            <a className="underline" href={capabilities.data.broker.action_url}>
                              Connect broker
                            </a>
                          )}
                        </p>
                      )}
                    {sourceKind === 'public' && (
                      <p className="text-xs text-muted-foreground">
                        {capabilities.data.public.available
                          ? `${capabilities.data.public.date_from} – ${capabilities.data.public.date_to}`
                          : 'NSE archive is unavailable.'}
                      </p>
                    )}
                  </>
                ) : capabilities.isError ? (
                  <p>
                    Connection status unavailable.{' '}
                    <Button variant="ghost" onClick={() => capabilities.refetch()}>
                      Retry
                    </Button>
                  </p>
                ) : (
                  <p>Checking connection…</p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="saved-source">Saved signals and prices</Label>
                <select
                  id="saved-source"
                  disabled={upload.isPending}
                  className="w-full min-w-0 rounded-md border bg-background p-2 text-sm"
                  value={source?.id || ''}
                  onChange={(e) => {
                    setDraft((current) => ({
                      ...current,
                      source: sources.data?.find((item) => item.id === e.target.value) || null,
                    }))
                    upload.reset()
                    setPreparationId('')
                  }}
                >
                  <option value="">Choose saved source</option>
                  {source && !sources.data?.some((item) => item.id === source.id) && (
                    <option value={source.id}>
                      Current frozen source · {source.receipt.date_from} to {source.receipt.date_to}
                    </option>
                  )}
                  {sources.data?.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.receipt.date_from} to {item.receipt.date_to} ·{' '}
                      {item.receipt.signal_count} signals ·{' '}
                      {item.provenance.synthetic ? 'Synthetic' : item.provenance.provider}
                    </option>
                  ))}
                </select>
              </div>
              <details open={!source}>
                <summary className="cursor-pointer font-medium">Upload a dated scanner CSV</summary>
                <div className="mt-4 space-y-4">
                  <p className="text-sm text-muted-foreground">
                    CSV with Symbol and Date or Timestamp columns.{' '}
                    <a
                      className="underline"
                      href="data:text/csv;charset=utf-8,Date%2CSymbol%0A2026-01-05%2CRELIANCE%0A2026-01-06%2CTCS%0A"
                      download="scanner-demo-signals.csv"
                    >
                      Download example signal CSV
                    </a>
                  </p>
                  <details>
                    <summary className="cursor-pointer text-sm text-muted-foreground">
                      Other data sources
                    </summary>
                    <div className="mt-3 space-y-2">
                      <Label htmlFor="research-source">Price source</Label>
                      <select
                        id="research-source"
                        disabled={upload.isPending || !!preparationId}
                        className="w-full rounded-md border bg-background p-2 text-sm"
                        value={sourceKind}
                        onChange={(e) => {
                          setSourceKind(e.target.value)
                          setDraft((current) => ({ ...current, source: null }))
                          upload.reset()
                        }}
                      >
                        <option value="public">NSE archive</option>
                        <option value="broker">OpenAlgo prices (automatic)</option>
                        <option value="fixture">Demo (synthetic)</option>
                        <option value="historify">Stored prices only</option>
                      </select>
                    </div>
                  </details>
                  <Label htmlFor="research-csv">Dated scanner CSV</Label>
                  <Input
                    id="research-csv"
                    disabled={upload.isPending || !!preparationId}
                    type="file"
                    accept=".csv,text/csv"
                    onChange={(e) => {
                      setFile(e.target.files?.[0] || null)
                      setDraft((current) => ({ ...current, source: null }))
                      upload.reset()
                    }}
                  />
                  <Button
                    className="h-auto whitespace-normal"
                    disabled={
                      !file ||
                      !!engineIssue ||
                      upload.isPending ||
                      !!preparationId ||
                      health.data?.maintenance ||
                      (sourceKind === 'broker' &&
                        !capabilities.data?.history?.can_prepare &&
                        capabilities.data?.broker.state !== 'ready_to_download')
                    }
                    onClick={() => upload.mutate()}
                  >
                    {upload.isPending ? 'Validating…' : 'Prepare prices'}
                  </Button>
                </div>
              </details>
              {preparationId && (
                <div className="space-y-2">
                  <output className="block text-sm">
                    Preparing prices: {preparation.data?.status || 'queued'} ·{' '}
                    {preparation.data?.progress || 0}%
                  </output>
                  <Button variant="outline" onClick={() => setParams({ job: preparationId })}>
                    Open preparation job
                  </Button>
                  {preparation.data?.error && <p role="alert">{preparation.data.error}</p>}
                  {preparation.data &&
                    !preparation.data.resumable &&
                    ['failed', 'cancelled', 'interrupted'].includes(preparation.data.status) && (
                      <Button
                        disabled={retry.isPending || health.data?.maintenance}
                        onClick={() => retry.mutate(preparationId)}
                      >
                        Retry preparation
                      </Button>
                    )}
                  {preparation.data &&
                    !active(preparation.data.status) &&
                    !preparation.data.result?.prepared_source && (
                      <Button variant="outline" onClick={() => setPreparationId('')}>
                        Choose another source
                      </Button>
                    )}
                </div>
              )}
              {sourceKind === 'public' && (
                <details>
                  <summary className="cursor-pointer font-medium">
                    Extend reviewed official evidence
                  </summary>
                  <div className="mt-3 space-y-3">
                    <p className="text-sm">
                      Update the available official prices, then retry source preparation. Existing
                      sources stay unchanged.
                    </p>
                    <p className="text-sm">
                      {capabilities.data?.evidence_update_available
                        ? 'A reviewed extension is configured.'
                        : 'No reviewed extension is configured. Ask the instance operator to configure the extension evidence and calendar before requesting newer prices.'}
                    </p>
                    <Label htmlFor="evidence-end-date">Requested official evidence end date</Label>
                    <Input
                      id="evidence-end-date"
                      type="date"
                      value={updateEnd}
                      onChange={(event) => setUpdateEnd(event.target.value)}
                    />
                    <Button
                      className="h-auto whitespace-normal"
                      disabled={
                        !updateEnd ||
                        !(source?.id || preparation.data?.source_id) ||
                        !capabilities.data?.evidence_update_available ||
                        evidenceUpdate.isPending ||
                        health.data?.maintenance
                      }
                      onClick={() => evidenceUpdate.mutate()}
                    >
                      Queue official evidence update
                    </Button>
                  </div>
                </details>
              )}
              {source && (
                <div className="space-y-4 border-t pt-4">
                  <output className="block text-sm font-medium">
                    Validated {source.receipt.signal_count} signals across{' '}
                    {source.receipt.symbol_count} symbols
                  </output>
                  <p className="text-xs text-muted-foreground">
                    {providerLabel(source.provenance)} · {source.receipt.date_from} –{' '}
                    {source.receipt.date_to}
                  </p>
                  {source.coverage.status === 'blocked' && (
                    <p role="alert" className="text-sm text-destructive">
                      Price coverage is incomplete. Choose another source.
                    </p>
                  )}
                  {source.coverage.status === 'warning' && (
                    <details>
                      <summary className="cursor-pointer text-xs text-muted-foreground">
                        Data notes
                      </summary>
                      <ul className="mt-2 list-disc pl-4 text-xs">
                        {Array.isArray(source.coverage.warnings) &&
                          source.coverage.warnings.map((warning, index) => (
                            <li key={index}>{String(warning)}</li>
                          ))}
                      </ul>
                    </details>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>2. Settings</CardTitle>
            </CardHeader>
            <CardContent>
              <form
                className="space-y-5"
                onSubmit={(event) => {
                  event.preventDefault()
                  if (engineIssue) return
                  if (
                    draft.kind === 'research' &&
                    (!draft.research.train_end ||
                      !draft.research.test_end ||
                      draft.research.train_end >= draft.research.test_end)
                  ) {
                    setRequestError(
                      'Choose an earlier-period cutoff before the final later-period cutoff. Both must be recorded exchange sessions.'
                    )
                    return
                  }
                  setRequestError('')
                  preflight.mutate(payload)
                }}
              >
                <ConnectorSettings draft={draft} catalog={capabilities.data?.connectors} />
                <ResearchSetup draft={draft} onChange={setDraft} />
                <Button
                  type="submit"
                  disabled={
                    !source ||
                    !!preparationId ||
                    upload.isPending ||
                    preflight.isPending ||
                    !!engineIssue
                  }
                >
                  Review and run
                </Button>
                {!source && (
                  <p className="text-xs text-muted-foreground">Upload your signals to continue.</p>
                )}
                {preflight.data && preflightSignature === signature && (
                  <div className="space-y-4 rounded-md border p-4">
                    <h3 className="font-medium">3. Run</h3>
                    <details>
                      <summary className="cursor-pointer text-sm text-muted-foreground">
                        Review dates and assumptions
                      </summary>
                      <div className="mt-4">
                        <PreflightReview receipt={preflight.data} payload={payload} />
                      </div>
                    </details>
                    <Button
                      type="button"
                      disabled={submit.isPending || health.data?.maintenance || !!engineIssue}
                      onClick={() => submit.mutate(submissionPayload)}
                    >
                      {submit.isPending
                        ? 'Submitting…'
                        : submit.isError
                          ? 'Retry same submission'
                          : terminalRetry
                            ? 'Retry run'
                            : priorSubmission?.status === 'completed'
                              ? 'Reopen completed run'
                              : draft.kind === 'backtest'
                                ? 'Run historical setup'
                                : 'Run research'}
                    </Button>
                  </div>
                )}
              </form>
            </CardContent>
          </Card>
        </div>
      )}
      <Dialog open={savedOpen} onOpenChange={setSavedOpen}>
        <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Saved runs</DialogTitle>
            <DialogDescription>Find and reopen a previous run.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <Label htmlFor="saved-search">Find saved work</Label>
                <Input
                  id="saved-search"
                  placeholder="Source, title, date or job identity"
                  value={savedFilter.q}
                  onChange={(event) => {
                    setSavedFilter((current) => ({ ...current, q: event.target.value }))
                    setSavedCursor('')
                    setCursorHistory([])
                  }}
                />
              </div>
              <div>
                <Label htmlFor="saved-kind">Investigation type</Label>
                <select
                  id="saved-kind"
                  className="w-full rounded-md border bg-background p-2 text-sm"
                  value={savedFilter.kind}
                  onChange={(event) => {
                    setSavedFilter((current) => ({ ...current, kind: event.target.value }))
                    setSavedCursor('')
                    setCursorHistory([])
                  }}
                >
                  <option value="">All types</option>
                  {[
                    'backtest',
                    'optimize',
                    'research',
                    'sensitivity',
                    'prepare_source',
                    'acquire',
                    'evidence_update',
                  ].map((kind) => (
                    <option key={kind} value={kind}>
                      {kind.replaceAll('_', ' ')}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <Label htmlFor="saved-status">Status</Label>
                <select
                  id="saved-status"
                  className="w-full rounded-md border bg-background p-2 text-sm"
                  value={savedFilter.status}
                  onChange={(event) => {
                    setSavedFilter((current) => ({ ...current, status: event.target.value }))
                    setSavedCursor('')
                    setCursorHistory([])
                  }}
                >
                  <option value="">All statuses</option>
                  {['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'].map(
                    (status) => (
                      <option key={status}>{status}</option>
                    )
                  )}
                </select>
              </div>
            </div>
            {jobs.isPending && <output>Loading saved runs…</output>}
            {jobs.data?.items.length === 0 && (
              <p className="text-sm text-muted-foreground">
                Submitted runs appear here and reopen from recorded evidence.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {jobs.data?.items.map((saved) => (
                <Button
                  className="h-auto max-w-full whitespace-normal break-words text-left"
                  key={saved.id}
                  variant={saved.id === selectedId ? 'default' : 'outline'}
                  onClick={() => {
                    setParams({ job: saved.id })
                    setSavedOpen(false)
                  }}
                >
                  <span>
                    {saved.title || `${saved.kind || 'Backtest'} · ${saved.source_id.slice(0, 8)}`}
                    <br />
                    {new Date(
                      typeof saved.created_at === 'number'
                        ? saved.created_at * 1000
                        : saved.created_at
                    ).toLocaleString()}{' '}
                    · {saved.status}
                    {saved.config?.target_pct != null
                      ? ` · Target ${saved.config.target_pct}% / stop ${saved.config.stop_pct}% / ${saved.config.hold_sessions} sessions`
                      : ''}
                  </span>
                </Button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="outline"
                disabled={!cursorHistory.length}
                onClick={() => {
                  setSavedCursor(cursorHistory[cursorHistory.length - 1] || '')
                  setCursorHistory((current) => current.slice(0, -1))
                }}
              >
                Newer results
              </Button>
              <span className="text-sm">
                Page {cursorHistory.length + 1} · {jobs.data?.items.length || 0} matching records
              </span>
              <Button
                variant="outline"
                disabled={!jobs.data?.next_cursor}
                onClick={() => {
                  setCursorHistory((current) => [...current, savedCursor])
                  setSavedCursor(jobs.data?.next_cursor || '')
                }}
              >
                Older results
              </Button>
            </div>
            <Button
              variant="ghost"
              onClick={() => {
                jobs.refetch()
                sources.refetch()
              }}
            >
              Refresh saved work
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
