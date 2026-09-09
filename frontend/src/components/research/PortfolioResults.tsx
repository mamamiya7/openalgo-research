import { useMemo, useState } from 'react'
import type { PortfolioJob, PortfolioResult, PortfolioSettings } from '@/api/portfolioResearch'
import { PortfolioLineChart } from '@/components/portfolio/PortfolioLineChart'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

const number = (value: unknown, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`
    : '—'
export const portfolioMoney = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('en-IN', { style: 'currency', currency: 'INR' })
    : '—'
const stamp = (value: unknown) =>
  typeof value === 'string' ? value.replace('T', ' ').replace('+05:30', ' IST') : '—'
const td = 'whitespace-nowrap px-3 py-3 text-left text-sm'
const th = `${td} text-xs font-medium text-muted-foreground`
function Pager({
  page,
  count,
  onChange,
}: {
  page: number
  count: number
  onChange: (page: number) => void
}) {
  if (count <= 25) return null
  return (
    <div className="flex items-center justify-end gap-3 pt-3 text-xs text-muted-foreground">
      <span>
        {page * 25 + 1}–{Math.min(count, (page + 1) * 25)} of {count}
      </span>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={page === 0}
        onClick={() => onChange(page - 1)}
      >
        Previous
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={(page + 1) * 25 >= count}
        onClick={() => onChange(page + 1)}
      >
        Next
      </Button>
    </div>
  )
}
function StrategySettings({ strategy }: { strategy: PortfolioSettings }) {
  const config = strategy.config
  const settings = [
    ['Allocation', number(strategy.allocation_pct, '%')],
    ['Per-trade size', number(config.order_size_pct, '% of allocation')],
    ['Profit target', number(config.target_pct, '%')],
    ['Stop loss', number(config.stop_pct, '%')],
    [
      'Holding limit',
      config.hold_minutes != null
        ? number(config.hold_minutes, ' minutes')
        : number(config.hold_sessions, ' trading sessions'),
    ],
    ['Close within the day', config.trade_horizon === 'intraday' ? 'Yes' : 'No'],
    ['Trailing stop', config.trailing_enabled ? number(config.trailing_pct, '%') : 'Off'],
    ['Costs per side', number(config.cost_bps, ' bps')],
    ['Slippage per side', number(config.slippage_bps, ' bps')],
    ...(config.entry_time ? [['Enter after', `${config.entry_time} IST`]] : []),
    ...(config.exit_time ? [['Exit by', `${config.exit_time} IST`]] : []),
  ]
  return (
    <div className="space-y-3 py-5">
      <h3 className="font-medium">{strategy.name}</h3>
      <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
        {settings.map(([label, value]) => (
          <div className="flex justify-between gap-3" key={label}>
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="text-right tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
interface ResultProps {
  job: PortfolioJob
  result: PortfolioResult
  onRerun: (trialId?: string) => void
  rerunning: boolean
  exportUrl: string
}
export function PortfolioResults(props: ResultProps) {
  const [period, setPeriod] = useState<'earlier' | 'later'>('earlier')
  const validation = props.result.validation
  if (!validation) return <PortfolioReport {...props} />
  const result =
    period === 'later'
      ? {
          ...validation.result,
          portfolio: props.result.portfolio,
          source: validation.result.source ?? props.result.source,
        }
      : props.result
  return (
    <div className="space-y-5">
      <fieldset className="inline-flex rounded-lg bg-muted p-1" aria-label="Evaluation period">
        {(['earlier', 'later'] as const).map((value) => (
          <Button
            type="button"
            key={value}
            size="sm"
            variant={period === value ? 'secondary' : 'ghost'}
            aria-pressed={period === value}
            onClick={() => setPeriod(value)}
          >
            {value === 'earlier' ? 'Earlier period' : 'Later period'}
          </Button>
        ))}
      </fieldset>
      <PortfolioReport key={period} {...props} result={result} laterPeriod={period === 'later'} />
    </div>
  )
}
function PortfolioReport({
  job,
  result,
  onRerun,
  rerunning,
  exportUrl,
  laterPeriod = false,
}: ResultProps & { laterPeriod?: boolean }) {
  const [tab, setTab] = useState('summary')
  const [strategyFilter, setStrategyFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [tradePage, setTradePage] = useState(0)
  const [trialPage, setTrialPage] = useState(0)
  const [trialDetail, setTrialDetail] = useState<string | null>(null)
  const summary = result.summary
  const experiment = result.experiment
  const series = useMemo(
    () => [
      {
        name: 'Portfolio equity',
        color: '#10b981',
        area: true,
        data: result.equity_curve.map((point) => ({
          date: point.timestamp ?? point.date,
          value: point.equity,
        })),
      },
    ],
    [result.equity_curve]
  )
  const trades = useMemo(
    () =>
      result.ledger.filter(
        (trade) =>
          (strategyFilter === 'all' || trade.strategy_id === strategyFilter) &&
          (statusFilter === 'all' || trade.status === statusFilter) &&
          (!query || String(trade.symbol).toLowerCase().includes(query.toLowerCase()))
      ),
    [result.ledger, strategyFilter, statusFilter, query]
  )
  const pending = Number(summary.pending_trades ?? 0) + Number(summary.unfunded_pending ?? 0)
  const otherExclusions = Math.max(
    0,
    Number(summary.excluded_signals ?? 0) - Number(result.source?.excluded_signals ?? 0)
  )
  const metrics = [
    ['Final equity', portfolioMoney(summary.final_equity)],
    ['Net P&L', portfolioMoney(summary.net_pnl)],
    ['Return', number(summary.net_return_pct, '%')],
    ['Max drawdown', number(summary.max_drawdown_pct, '%')],
    ['Closed trades', number(summary.closed_trades)],
    ['Available cash', portfolioMoney(result.equity_curve.at(-1)?.cash)],
  ]
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">
            {result.portfolio?.name ?? job.specification?.portfolio?.name ?? 'Portfolio result'}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {result.source?.provider ?? 'OpenAlgo prices'} ·{' '}
            {(result.source?.interval ?? result.execution?.interval) === '1m'
              ? 'Minute candles'
              : 'Daily candles'}
            {result.equity_curve.length
              ? ` · ${result.equity_curve[0].date} – ${result.equity_curve.at(-1)?.date}`
              : ''}
          </p>
        </div>
        <div className="flex gap-2">
          {!laterPeriod && (
            <Button type="button" variant="outline" disabled={rerunning} onClick={() => onRerun()}>
              {rerunning ? 'Starting…' : 'Run again'}
            </Button>
          )}
          <Button variant="ghost" asChild>
            <a href={exportUrl} download>
              Export
            </a>
          </Button>
        </div>
      </div>
      {Number(result.source?.excluded_signals) > 0 && (
        <button
          type="button"
          className="text-left text-sm text-muted-foreground underline-offset-4 hover:underline"
          onClick={() => {
            setStatusFilter('excluded')
            setStrategyFilter('all')
            setQuery('')
            setTradePage(0)
            setTab('trades')
          }}
        >
          {result.source?.excluded_signals}{' '}
          {result.source?.excluded_signals === 1 ? 'signal' : 'signals'} excluded — view reasons
        </button>
      )}
      {experiment && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <span className="font-medium">
            Selected from {experiment.counts.evaluated_this_pass} tested portfolios
          </span>
          <span className="text-muted-foreground">
            {experiment.specification.objective === 'return'
              ? 'Highest return'
              : experiment.specification.objective === 'drawdown'
                ? 'Lowest drawdown'
                : 'Balanced return & drawdown'}
          </span>
        </div>
      )}
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="trades">Trades</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
          {experiment && <TabsTrigger value="trials">Trials</TabsTrigger>}
        </TabsList>
        <TabsContent value="summary" className="space-y-7 pt-5">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3 xl:grid-cols-6">
            {metrics.map(([label, value]) => (
              <div key={label}>
                <dt className="mb-1 text-xs text-muted-foreground">{label}</dt>
                <dd className="text-xl font-semibold tabular-nums tracking-tight">{value}</dd>
              </div>
            ))}
          </dl>
          <PortfolioLineChart series={series} height={340} format={portfolioMoney} />
          <section className="space-y-3" aria-labelledby="portfolio-contribution-heading">
            <h3 id="portfolio-contribution-heading" className="font-medium">
              Strategy contributions
            </h3>
            <div className="overflow-x-auto rounded-lg border">
              <table className="w-full">
                <thead className="bg-muted/40">
                  <tr>
                    {[
                      'Strategy',
                      'Allocation',
                      'Net P&L',
                      'Return contribution',
                      'Closed trades',
                    ].map((label) => (
                      <th className={th} key={label}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {result.per_strategy.map((strategy) => (
                    <tr key={strategy.id}>
                      <td className={td}>
                        <button
                          type="button"
                          className="font-medium underline-offset-4 hover:underline focus-visible:underline"
                          onClick={() => {
                            setStrategyFilter(strategy.id)
                            setTradePage(0)
                            setTab('trades')
                          }}
                        >
                          {strategy.name}
                        </button>
                      </td>
                      <td className={td}>{number(strategy.allocation_pct, '%')}</td>
                      <td className={td}>{portfolioMoney(strategy.net_pnl)}</td>
                      <td className={td} title="Percentage points of the portfolio return">
                        {number(strategy.contribution_pct, ' pp')}
                      </td>
                      <td className={td}>{number(strategy.summary.closed_trades)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          {(pending > 0 || Number(summary.skipped_trades) > 0 || otherExclusions > 0) && (
            <Button
              type="button"
              variant="link"
              className="h-auto px-0 text-muted-foreground"
              onClick={() => setTab('trades')}
            >
              {[
                pending > 0 ? `${pending} pending` : '',
                Number(summary.skipped_trades) > 0 ? `${summary.skipped_trades} skipped` : '',
                otherExclusions > 0 ? `${otherExclusions} excluded` : '',
              ]
                .filter(Boolean)
                .join(' · ')}{' '}
              — view trades
            </Button>
          )}
        </TabsContent>
        <TabsContent value="trades" className="space-y-4 pt-4">
          <div className="flex flex-wrap gap-3">
            <Input
              className="sm:w-44"
              aria-label="Find symbol"
              placeholder="Find symbol"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value)
                setTradePage(0)
              }}
            />
            <select
              className="h-10 rounded-md border bg-background px-3 text-sm"
              aria-label="Filter trades by strategy"
              value={strategyFilter}
              onChange={(event) => {
                setStrategyFilter(event.target.value)
                setTradePage(0)
              }}
            >
              <option value="all">All strategies</option>
              {result.strategies.map((strategy) => (
                <option key={strategy.id} value={strategy.id}>
                  {strategy.name}
                </option>
              ))}
            </select>
            <select
              className="h-10 rounded-md border bg-background px-3 text-sm"
              aria-label="Filter trades by status"
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value)
                setTradePage(0)
              }}
            >
              <option value="all">All outcomes</option>
              <option value="closed">Closed</option>
              <option value="pending">Pending</option>
              <option value="skipped">Skipped</option>
              <option value="excluded">Excluded</option>
            </select>
          </div>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full">
              <thead className="bg-muted/40">
                <tr>
                  {['Strategy / symbol', 'Entry', 'Exit', 'Quantity', 'Net P&L', 'Outcome'].map(
                    (label) => (
                      <th key={label} className={th}>
                        {label}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody className="divide-y">
                {trades.slice(tradePage * 25, (tradePage + 1) * 25).map((trade, index) => (
                  <tr key={`${trade.strategy_id}-${trade.source_row}-${trade.symbol}-${index}`}>
                    <td className={td}>
                      <span className="font-medium">{trade.symbol}</span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {trade.strategy_name}
                      </span>
                    </td>
                    <td className={td}>
                      {stamp(trade.entry_timestamp ?? trade.entry_date)}
                      <span className="block text-xs text-muted-foreground">
                        {portfolioMoney(trade.entry_price)}
                      </span>
                    </td>
                    <td className={td}>
                      {stamp(trade.exit_timestamp ?? trade.exit_date)}
                      <span className="block text-xs text-muted-foreground">
                        {portfolioMoney(trade.exit_price)}
                      </span>
                    </td>
                    <td className={td}>{number(trade.quantity)}</td>
                    <td className={td}>{portfolioMoney(trade.pnl)}</td>
                    <td className={td}>
                      <details>
                        <summary className="cursor-pointer capitalize">{trade.status}</summary>
                        <p className="mt-2 max-w-60 whitespace-normal text-xs text-muted-foreground">
                          {String(trade.reason ?? '')}
                        </p>
                      </details>
                    </td>
                  </tr>
                ))}
                {!trades.length && (
                  <tr>
                    <td colSpan={6} className="p-8 text-center text-sm text-muted-foreground">
                      No trades match these filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager page={tradePage} count={trades.length} onChange={setTradePage} />
        </TabsContent>
        <TabsContent value="settings" className="pt-4">
          <div className="space-y-1 border-b pb-4 text-sm">
            <p>Shared starting cash: {portfolioMoney(summary.initial_capital)}</p>
            <p className="text-muted-foreground">
              {(result.execution?.engine ?? result.portfolio?.engine) === 'nautilus'
                ? 'NautilusTrader'
                : 'VectorBT'}
              {result.execution?.engine_version ? ` ${result.execution.engine_version}` : ''}
              {experiment ? ` · Optuna ${experiment.optimizer.version}` : ''}
            </p>
          </div>
          <div className="divide-y">
            {result.strategies.map((strategy) => (
              <StrategySettings key={strategy.id} strategy={strategy} />
            ))}
          </div>
        </TabsContent>
        {experiment && (
          <TabsContent value="trials" className="space-y-4 pt-4">
            <p className="text-sm text-muted-foreground">
              {experiment.counts.evaluated_this_pass} distinct portfolios ·{' '}
              {experiment.counts.rejected_allocations} allocation combinations excluded
              {experiment.counts.reused_trials
                ? ` · ${experiment.counts.reused_trials} repeated proposals reused`
                : ''}
            </p>
            <div className="overflow-x-auto rounded-lg border">
              <table className="w-full">
                <thead className="bg-muted/40">
                  <tr>
                    {['Trial', 'Return', 'Max drawdown', 'Closed trades', ''].map((label) => (
                      <th className={th} key={label}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {experiment.rows.slice(trialPage * 25, (trialPage + 1) * 25).map((trial) => (
                    <tr key={trial.config_id}>
                      <td className={td}>
                        {trial.trial_number + 1}
                        {trial.config_id === experiment.recommendation_id && (
                          <span className="ml-2 text-xs text-primary">Selected</span>
                        )}
                      </td>
                      <td className={td}>{number(trial.summary.net_return_pct, '%')}</td>
                      <td className={td}>{number(trial.summary.max_drawdown_pct, '%')}</td>
                      <td className={td}>{number(trial.summary.closed_trades)}</td>
                      <td className={td}>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            setTrialDetail(trialDetail === trial.config_id ? null : trial.config_id)
                          }
                        >
                          View settings
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={rerunning}
                          onClick={() => onRerun(trial.config_id)}
                        >
                          Backtest this
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={trialPage} count={experiment.rows.length} onChange={setTrialPage} />
            {trialDetail && (
              <section
                aria-label="Trial strategy settings"
                className="divide-y rounded-lg border px-4"
              >
                {experiment.rows
                  .find((trial) => trial.config_id === trialDetail)
                  ?.strategies.map((strategy) => (
                    <StrategySettings key={strategy.id} strategy={strategy} />
                  ))}
              </section>
            )}
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
