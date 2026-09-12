import { FileSpreadsheet, Plus, Settings2, Sparkles, Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import type {
  PortfolioAxis,
  PortfolioCapabilities,
  PortfolioRequest,
  PortfolioStrategy,
} from '@/api/portfolioResearch'
import type { ResearchSource } from '@/api/scannerResearch'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { researchDefaults } from '@/lib/researchDraft'
import { ChartinkSource } from './ChartinkSource'
import { PortfolioCsvHelp } from './PortfolioCsvHelp'
import { PortfolioSetupReview } from './PortfolioSetupReview'
import { PortfolioStrategySettings, portfolioSearchRange } from './PortfolioStrategySettings'
import { type ResearchUploadActivity, ResearchUploadProgress } from './ResearchRunProgress'
import { strategyRuleSummary } from './researchPresentation'

export interface PortfolioDraft {
  portfolio: PortfolioRequest
  optimizing: boolean
  equalWeights: boolean
  sources: Record<string, ResearchSource & { filename?: string }>
  optimization: NonNullable<PortfolioRequest['optimization']>
}
export function freshPortfolioDraft(): PortfolioDraft {
  return {
    portfolio: {
      version: 'research-portfolio-v1',
      name: 'My portfolio',
      capital: 100000,
      engine: 'vectorbt',
      strategies: [],
    },
    optimizing: false,
    equalWeights: true,
    sources: {},
    optimization: { sampler: 'tpe', trials: 25, objective: 'balanced', seed: 0 },
  }
}
export function equalSplit(strategies: PortfolioStrategy[]): PortfolioStrategy[] {
  if (!strategies.length) return []
  const amount = Math.floor(10000 / strategies.length) / 100
  return strategies.map((strategy, index) => ({
    ...strategy,
    allocation_pct:
      index === strategies.length - 1 ? Number((100 - amount * index).toFixed(2)) : amount,
  }))
}
function activeSearch(strategy: PortfolioStrategy): PortfolioStrategy['search'] {
  const search = { ...strategy.search }
  if (strategy.config.hold_minutes != null || strategy.config.trade_horizon === 'intraday')
    delete search.hold_sessions
  if (strategy.config.hold_minutes == null) delete search.hold_minutes
  if (!strategy.config.trailing_enabled) delete search.trailing_pct
  return search
}
const searchLabels: Record<PortfolioAxis, string> = {
  target_pct: 'Profit target',
  stop_pct: 'Stop loss',
  hold_sessions: 'Holding sessions',
  hold_minutes: 'Holding minutes',
  trailing_pct: 'Trailing stop',
  order_size_pct: 'Trade size',
  allocation_pct: 'Allocation',
}
export function suggestedStrategySearch(
  strategy: PortfolioStrategy,
  engine: PortfolioRequest['engine']
): PortfolioStrategy['search'] {
  const fields: Array<Exclude<PortfolioAxis, 'allocation_pct'>> = ['target_pct', 'stop_pct']
  if (strategy.config.hold_minutes != null) fields.unshift('hold_minutes')
  else if (strategy.config.trade_horizon !== 'intraday') fields.unshift('hold_sessions')
  if (strategy.config.trailing_enabled && engine === 'vectorbt') fields.push('trailing_pct')
  return Object.fromEntries(
    fields.flatMap((field) => {
      const value = Number(strategy.config[field])
      return Number.isFinite(value) && value > 0
        ? [[field, portfolioSearchRange(field, value)]]
        : []
    })
  )
}
export function applySuggestedSearch(draft: PortfolioDraft, onlyEmpty = false): PortfolioDraft {
  return {
    ...draft,
    portfolio: {
      ...draft.portfolio,
      strategies: draft.portfolio.strategies.map((strategy) => {
        if (onlyEmpty && Object.keys(strategy.search).length > 0) return strategy
        return {
          ...strategy,
          search: {
            ...suggestedStrategySearch(strategy, draft.portfolio.engine),
            ...strategy.search,
          },
        }
      }),
    },
  }
}
function searchDescription(strategy: PortfolioStrategy): string {
  return (
    Object.entries(activeSearch(strategy)) as Array<
      [PortfolioAxis, { min: number; max: number; step: number }]
    >
  )
    .map(
      ([field, range]) =>
        `${searchLabels[field]} ${range.min}–${range.max}${field.endsWith('_pct') ? '%' : ''} (step ${range.step})`
    )
    .join(' · ')
}
function sourceSummary(source: ResearchSource | undefined): string {
  if (!source) return 'Saved signal file'
  const receipt = source.receipt
  const count = (value: number, word: string) =>
    `${value.toLocaleString('en-IN')} ${word}${value === 1 ? '' : 's'}`
  const parts = [count(receipt.signal_count, 'signal'), count(receipt.symbol_count, 'symbol')]
  const from = new Date(receipt.date_from)
  const through = new Date(receipt.date_to)
  if (Number.isFinite(from.getTime()) && Number.isFinite(through.getTime()) && from <= through) {
    parts.push(
      new Intl.DateTimeFormat('en-IN', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        timeZone: 'UTC',
      })
        .formatRange(from, through)
        .replace(/\s*–\s*/g, '–')
    )
  }
  return parts.join(' · ')
}
export function addPortfolioSource(
  draft: PortfolioDraft,
  source: ResearchSource,
  filename: string
): PortfolioDraft {
  if (draft.portfolio.strategies.length >= 8) return draft
  const remaining = Math.max(
    0,
    100 - draft.portfolio.strategies.reduce((sum, item) => sum + item.allocation_pct, 0)
  )
  const strategy: PortfolioStrategy = {
    id: crypto.randomUUID(),
    name: filename.replace(/\.csv$/i, '').slice(0, 80) || 'Strategy',
    type: 'signals',
    source_id: source.id,
    allocation_pct: remaining,
    config: structuredClone(researchDefaults),
    search: {},
  }
  if (draft.optimizing) strategy.search = suggestedStrategySearch(strategy, draft.portfolio.engine)
  const strategies = [...draft.portfolio.strategies, strategy]
  return {
    ...draft,
    sources: { ...draft.sources, [source.id]: { ...source, filename } },
    portfolio: {
      ...draft.portfolio,
      strategies: draft.equalWeights ? equalSplit(strategies) : strategies,
    },
  }
}
export function portfolioPayload(draft: PortfolioDraft): PortfolioRequest {
  const { optimization: _discarded, validation, automatic_research, ...portfolio } = draft.portfolio
  const automatic = draft.optimizing && Boolean(automatic_research)
  return {
    ...portfolio,
    strategies: portfolio.strategies.map((strategy) => ({
      ...strategy,
      config: { ...strategy.config, initial_capital: portfolio.capital },
      search: draft.optimizing && !automatic ? activeSearch(strategy) : {},
    })),
    ...(automatic
      ? { automatic_research }
      : draft.optimizing
        ? { optimization: draft.optimization }
        : {}),
    ...(!automatic && validation && (draft.optimizing || validation.mode === 'reserve')
      ? { validation }
      : {}),
  }
}
export function portfolioDraftIssue(draft: PortfolioDraft): string | null {
  const portfolio = draft.portfolio
  if (!portfolio.strategies.length) return 'Add a strategy CSV to start.'
  if (!portfolio.name.trim()) return 'Give this portfolio a name.'
  if (!Number.isFinite(portfolio.capital) || portfolio.capital < 1 || portfolio.capital > 1e9)
    return 'Enter valid starting cash.'
  const allocation = portfolio.strategies.reduce((sum, item) => sum + item.allocation_pct, 0)
  if (!Number.isFinite(allocation) || allocation <= 0 || allocation > 100.00000001)
    return 'Strategy allocations must total more than 0% and at most 100%.'
  if (portfolio.strategies.some((strategy) => !strategy.name.trim()))
    return 'Give each strategy a name.'
  if (
    draft.optimizing &&
    !portfolio.automatic_research &&
    !portfolio.strategies.some((strategy) => Object.keys(activeSearch(strategy)).length)
  )
    return 'Choose at least one setting to optimize in a strategy’s Settings.'
  return null
}
export function portfolioEngineIssue(
  draft: PortfolioDraft,
  capabilities?: PortfolioCapabilities
): string | null {
  const engine = capabilities?.engines.find((item) => item.id === draft.portfolio.engine)
  if (capabilities && !engine?.available)
    return `${draft.portfolio.engine === 'nautilus' ? 'NautilusTrader' : 'VectorBT'} is unavailable in this installation.`
  if (
    capabilities &&
    draft.optimizing &&
    !capabilities.optimizers.some((item) => item.id === 'optuna' && item.available)
  )
    return 'Optuna is unavailable in this installation.'
  if (draft.portfolio.engine === 'nautilus') {
    if (
      draft.portfolio.strategies.some(
        (strategy) => strategy.config.slippage_bps !== 0 || strategy.config.trailing_enabled
      )
    )
      return 'To use NautilusTrader, set slippage to 0 and turn off trailing protection in each strategy’s Settings.'
    if (
      Number.isFinite(draft.portfolio.capital) &&
      Math.abs(draft.portfolio.capital * 100 - Math.round(draft.portfolio.capital * 100)) > 0.000001
    )
      return 'NautilusTrader requires starting cash with at most two decimal places.'
  }
  return null
}
export function PortfolioBuilder({
  draft,
  onChange,
  onUpload,
  onUseSaved,
  onRun,
  busy,
  uploading,
  uploadActivity,
  capabilities,
}: {
  draft: PortfolioDraft
  onChange: (draft: PortfolioDraft) => void
  onUpload: (files: File[]) => void
  onUseSaved: (opener: HTMLButtonElement) => void
  onRun: () => void
  busy: boolean
  uploading: boolean
  uploadActivity?: ResearchUploadActivity
  capabilities?: PortfolioCapabilities
}) {
  const input = useRef<HTMLInputElement>(null)
  const [settingsId, setSettingsId] = useState<string | null>(null)
  const settingsOpener = useRef<HTMLElement | null>(null)
  const openSettings = (id: string, opener: HTMLElement) => {
    settingsOpener.current = opener
    setSettingsId(id)
  }
  const portfolio = draft.portfolio
  const automatic = draft.optimizing && Boolean(portfolio.automatic_research)
  const manualSearch = draft.optimizing && !automatic
  const update = (changes: Partial<PortfolioRequest>) =>
    onChange({ ...draft, portfolio: { ...portfolio, ...changes } })
  const allocation = portfolio.strategies.reduce((sum, item) => sum + item.allocation_pct, 0)
  const activeStrategy = portfolio.strategies.find((strategy) => strategy.id === settingsId) ?? null
  const disabled = busy || uploading
  const engineIssue = portfolioEngineIssue(draft, capabilities)
  const engines =
    capabilities?.engines.filter((engine) => engine.available || engine.id === portfolio.engine) ??
    []
  return (
    <form
      className="space-y-7"
      onSubmit={(event) => {
        event.preventDefault()
        onRun()
      }}
    >
      {uploading && uploadActivity && <ResearchUploadProgress activity={uploadActivity} />}
      <fieldset disabled={disabled} className="space-y-7 disabled:opacity-70">
        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(180px,240px)]">
          <div className="space-y-2">
            <Label htmlFor="portfolio-name">Portfolio name</Label>
            <Input
              id="portfolio-name"
              value={portfolio.name}
              maxLength={100}
              required
              onChange={(event) => update({ name: event.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="portfolio-capital">Shared starting cash (₹)</Label>
            <Input
              id="portfolio-capital"
              type="number"
              min={1}
              max={1e9}
              step="any"
              required
              value={Number.isFinite(portfolio.capital) ? portfolio.capital : ''}
              onChange={(event) => update({ capital: event.target.valueAsNumber })}
            />
          </div>
        </div>
        <section aria-labelledby="portfolio-strategies-heading" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="portfolio-strategies-heading" className="font-medium">
              Strategies{' '}
              <span className="ml-1 text-sm font-normal text-muted-foreground">
                {portfolio.strategies.length ? portfolio.strategies.length : ''}
              </span>
            </h2>
            <div className="flex items-center gap-2">
              {portfolio.strategies.length > 1 && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    onChange({
                      ...draft,
                      equalWeights: true,
                      portfolio: { ...portfolio, strategies: equalSplit(portfolio.strategies) },
                    })
                  }
                >
                  Equal split
                </Button>
              )}
              {portfolio.strategies.length > 0 && portfolio.strategies.length < 8 && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  id="portfolio-saved-signals"
                  onClick={(event) => onUseSaved(event.currentTarget)}
                >
                  Use saved signals
                </Button>
              )}
              {portfolio.strategies.length > 0 && portfolio.strategies.length < 8 && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => input.current?.click()}
                >
                  <Plus className="h-4 w-4" aria-hidden="true" />
                  Add signal CSV
                </Button>
              )}
            </div>
          </div>
          <input
            ref={input}
            type="file"
            className="sr-only"
            tabIndex={-1}
            accept=".csv,text/csv"
            multiple
            aria-label="Upload strategy CSV files"
            onChange={(event) => {
              onUpload(Array.from(event.target.files ?? []))
              event.target.value = ''
            }}
          />
          {portfolio.strategies.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-5 py-12 text-center">
              <FileSpreadsheet className="h-8 w-8 text-muted-foreground" aria-hidden="true" />
              <p className="text-sm text-muted-foreground">
                Upload a signal CSV or use one you saved.
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                <Button type="button" onClick={() => input.current?.click()}>
                  <Plus className="h-4 w-4" aria-hidden="true" />
                  {uploading ? 'Adding signals…' : 'Add signal CSV'}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  id="portfolio-saved-signals"
                  onClick={(event) => onUseSaved(event.currentTarget)}
                >
                  Use saved signals
                </Button>
              </div>
            </div>
          ) : (
            <div className="divide-y rounded-xl border">
              {portfolio.strategies.map((strategy, index) => {
                const source = draft.sources[strategy.source_id]
                const range = manualSearch ? strategy.search.allocation_pct : undefined
                return (
                  <div
                    key={strategy.id}
                    className="grid gap-3 p-4 sm:grid-cols-[minmax(0,1fr)_120px_auto] sm:items-center motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-150"
                  >
                    <div className="min-w-0">
                      <Input
                        className="h-9 border-transparent px-0 font-medium shadow-none hover:border-input focus-visible:px-2"
                        aria-label={`Strategy ${index + 1} name`}
                        maxLength={80}
                        required
                        value={strategy.name}
                        onChange={(event) =>
                          update({
                            strategies: portfolio.strategies.map((item) =>
                              item.id === strategy.id ? { ...item, name: event.target.value } : item
                            ),
                          })
                        }
                      />
                      <p
                        className={`text-xs text-muted-foreground ${source?.receipt.chartink ? '' : 'truncate'}`}
                      >
                        {sourceSummary(source)}
                        {manualSearch && Object.keys(activeSearch(strategy)).length
                          ? ` · ${Object.keys(activeSearch(strategy)).length} settings vary`
                          : ''}
                      </p>
                      <ChartinkSource source={source?.receipt.chartink} />
                      <p className="mt-1 text-xs text-muted-foreground">
                        {strategyRuleSummary(strategy, manualSearch)}
                      </p>
                    </div>
                    {range ? (
                      <Button
                        type="button"
                        variant="outline"
                        aria-label={`Allocation range for ${strategy.name}`}
                        onClick={(event) => openSettings(strategy.id, event.currentTarget)}
                      >
                        {range.min}–{range.max}%
                      </Button>
                    ) : (
                      <div className="relative">
                        <Input
                          aria-label={`Allocation for ${strategy.name} (%)`}
                          type="number"
                          min={0}
                          max={100}
                          step="any"
                          required
                          className="pr-8"
                          value={
                            Number.isFinite(strategy.allocation_pct) ? strategy.allocation_pct : ''
                          }
                          onChange={(event) =>
                            onChange({
                              ...draft,
                              equalWeights: false,
                              portfolio: {
                                ...portfolio,
                                strategies: portfolio.strategies.map((item) =>
                                  item.id === strategy.id
                                    ? { ...item, allocation_pct: event.target.valueAsNumber }
                                    : item
                                ),
                              },
                            })
                          }
                        />
                        <span className="pointer-events-none absolute right-3 top-2.5 text-sm text-muted-foreground">
                          %
                        </span>
                      </div>
                    )}
                    <div className="flex items-center gap-1">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        aria-label={`Settings for ${strategy.name}`}
                        onClick={(event) => openSettings(strategy.id, event.currentTarget)}
                      >
                        <Settings2 className="h-4 w-4" aria-hidden="true" />
                        Settings
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={`Remove ${strategy.name}`}
                        onClick={() => {
                          const next = portfolio.strategies.filter(
                            (item) => item.id !== strategy.id
                          )
                          update({ strategies: draft.equalWeights ? equalSplit(next) : next })
                        }}
                      >
                        <Trash2 className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
          {portfolio.strategies.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                {Number.isFinite(allocation)
                  ? `${Number(allocation.toFixed(2))}% allocated${allocation < 100 ? ` · ${Number((100 - allocation).toFixed(2))}% held in cash` : ''}`
                  : 'Enter strategy allocations'}
              </span>
              <span>Prices from OpenAlgo</span>
            </div>
          )}
          <PortfolioCsvHelp />
        </section>
        <div className="space-y-4">
          <fieldset className="inline-flex rounded-lg bg-muted p-1" aria-label="Run type">
            {([false, true] as const).map((optimizing) => (
              <Button
                key={String(optimizing)}
                type="button"
                size="sm"
                variant={draft.optimizing === optimizing ? 'secondary' : 'ghost'}
                aria-pressed={draft.optimizing === optimizing}
                onClick={() =>
                  onChange(
                    optimizing
                      ? applySuggestedSearch({ ...draft, optimizing }, true)
                      : { ...draft, optimizing }
                  )
                }
              >
                {optimizing ? 'Optimize' : 'Backtest'}
              </Button>
            ))}
          </fieldset>
          {draft.optimizing && (
            <fieldset
              className="flex flex-wrap items-center gap-2"
              aria-label="Optimization approach"
            >
              {([true, false] as const).map((enabled) => (
                <Button
                  key={String(enabled)}
                  type="button"
                  size="sm"
                  variant={automatic === enabled ? 'secondary' : 'ghost'}
                  aria-pressed={automatic === enabled}
                  onClick={() =>
                    update({
                      automatic_research: enabled
                        ? { version: 'automatic-trade-management-v1' }
                        : undefined,
                    })
                  }
                >
                  {enabled && <Sparkles className="size-4" aria-hidden="true" />}
                  {enabled ? 'Automatic research' : 'Custom search'}
                </Button>
              ))}
            </fieldset>
          )}
          {automatic && (
            <div className="relative overflow-hidden rounded-xl border border-cyan-500/20 bg-gradient-to-br from-cyan-500/5 via-background to-violet-500/5 p-5 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300">
              <p className="text-sm font-medium">Find stronger trading settings</p>
              <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                Tests exits and holding periods, then checks later dates and higher costs.
              </p>
              <ol
                aria-label="Automatic research stages"
                className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted-foreground"
              >
                {['Baseline', 'Search', 'Check', 'Findings'].map((stage, index) => (
                  <li key={stage} className="flex items-center gap-2">
                    <span className="flex size-5 items-center justify-center rounded-full border border-cyan-500/25 text-foreground">
                      {index + 1}
                    </span>
                    {stage}
                  </li>
                ))}
              </ol>
            </div>
          )}
          {manualSearch && (
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_160px] motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-150">
              {portfolio.strategies.length > 0 && (
                <section aria-label="Optimization ranges" className="space-y-3 sm:col-span-2">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-medium">Settings to optimize</h3>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => onChange(applySuggestedSearch(draft))}
                    >
                      Suggested search
                    </Button>
                  </div>
                  <div className="divide-y">
                    {portfolio.strategies.map((strategy) => (
                      <div
                        key={strategy.id}
                        className="flex items-start justify-between gap-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-medium">{strategy.name}</p>
                          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                            {searchDescription(strategy) ||
                              'Choose Suggested search or select your own ranges.'}
                          </p>
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Edit search for ${strategy.name}`}
                          onClick={(event) => openSettings(strategy.id, event.currentTarget)}
                        >
                          Edit
                        </Button>
                      </div>
                    ))}
                  </div>
                </section>
              )}
              <div className="space-y-2">
                <Label htmlFor="portfolio-objective">Optimize for</Label>
                <select
                  id="portfolio-objective"
                  className="h-10 w-full rounded-md border bg-background px-3 text-sm"
                  value={draft.optimization.objective}
                  onChange={(event) =>
                    onChange({
                      ...draft,
                      optimization: {
                        ...draft.optimization,
                        objective: event.target.value as typeof draft.optimization.objective,
                      },
                    })
                  }
                >
                  <option value="balanced">Balanced return & drawdown</option>
                  <option value="return">Highest return</option>
                  <option value="drawdown">Lowest drawdown</option>
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="portfolio-trials">Trials</Label>
                <Input
                  id="portfolio-trials"
                  type="number"
                  min={1}
                  max={1000}
                  step={1}
                  required
                  value={
                    Number.isFinite(draft.optimization.trials) ? draft.optimization.trials : ''
                  }
                  onChange={(event) =>
                    onChange({
                      ...draft,
                      optimization: { ...draft.optimization, trials: event.target.valueAsNumber },
                    })
                  }
                />
              </div>
            </div>
          )}
          {!automatic && (
            <div className="space-y-1">
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-primary"
                  checked={Boolean(
                    portfolio.validation &&
                      (draft.optimizing || portfolio.validation.mode === 'reserve')
                  )}
                  onChange={(event) =>
                    update({
                      validation: event.target.checked
                        ? { train_pct: 80, mode: 'reserve' }
                        : undefined,
                    })
                  }
                />
                {draft.optimizing && portfolio.validation && portfolio.validation.mode !== 'reserve'
                  ? 'Check a later period'
                  : 'Reserve a later period'}
              </label>
              {portfolio.validation &&
                draft.optimizing &&
                portfolio.validation.mode !== 'reserve' && (
                  <p className="pl-6 text-xs text-muted-foreground">
                    This saved setup checks both periods during the run.
                  </p>
                )}
            </div>
          )}
          <PortfolioSetupReview draft={draft} />
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">More settings</summary>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {(['date_from', 'date_to'] as const).map((field) => (
                <div className="space-y-2" key={field}>
                  <Label htmlFor={`portfolio-${field}`}>
                    {field === 'date_from' ? 'From' : 'Through'}
                  </Label>
                  <Input
                    type="date"
                    id={`portfolio-${field}`}
                    value={portfolio[field] ?? ''}
                    onChange={(event) => update({ [field]: event.target.value || undefined })}
                  />
                </div>
              ))}
              {manualSearch && (
                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="portfolio-sampler">Search method</Label>
                  <select
                    id="portfolio-sampler"
                    className="h-10 w-full rounded-md border bg-background px-3 text-sm"
                    value={draft.optimization.sampler}
                    onChange={(event) =>
                      onChange({
                        ...draft,
                        optimization: {
                          ...draft.optimization,
                          sampler: event.target.value as 'tpe' | 'grid',
                        },
                      })
                    }
                  >
                    <option value="tpe">Adaptive · Optuna TPE</option>
                    <option value="grid">Grid · Optuna</option>
                  </select>
                </div>
              )}
              {(engines.length > 1 || portfolio.engine === 'nautilus') && (
                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="portfolio-engine">Backtest engine</Label>
                  <select
                    id="portfolio-engine"
                    className="h-10 w-full rounded-md border bg-background px-3 text-sm"
                    value={portfolio.engine}
                    onChange={(event) =>
                      update({ engine: event.target.value as PortfolioRequest['engine'] })
                    }
                  >
                    {engines.map((engine) => (
                      <option key={engine.id} value={engine.id} disabled={!engine.available}>
                        {engine.name}
                        {!engine.available ? ' (unavailable)' : ''}
                      </option>
                    ))}
                    {!engines.some((engine) => engine.id === portfolio.engine) && (
                      <option value={portfolio.engine}>
                        {portfolio.engine === 'nautilus' ? 'NautilusTrader' : 'VectorBT'}
                      </option>
                    )}
                  </select>
                  {portfolio.engine === 'nautilus' && (
                    <p className="text-xs text-muted-foreground">
                      A different fill model; results can differ.
                    </p>
                  )}
                </div>
              )}
            </div>
          </details>
        </div>
      </fieldset>
      <div className="flex flex-wrap items-center justify-end gap-4 border-t pt-5">
        {engineIssue && (
          <p role="alert" className="mr-auto max-w-xl text-sm text-destructive">
            {engineIssue}
          </p>
        )}
        <Button
          type="submit"
          size="lg"
          disabled={disabled || !portfolio.strategies.length || Boolean(engineIssue)}
        >
          {busy
            ? 'Starting…'
            : uploading
              ? 'Adding strategies…'
              : draft.optimizing
                ? automatic
                  ? 'Research settings'
                  : 'Run optimization'
                : 'Run backtest'}
        </Button>
      </div>
      <PortfolioStrategySettings
        strategy={activeStrategy}
        source={activeStrategy ? draft.sources[activeStrategy.source_id] : undefined}
        optimizing={manualSearch}
        onClose={() => setSettingsId(null)}
        onRestoreFocus={() => settingsOpener.current?.focus()}
        onChange={(strategy, allocationChanged) =>
          onChange({
            ...draft,
            equalWeights: allocationChanged ? false : draft.equalWeights,
            portfolio: {
              ...portfolio,
              strategies: portfolio.strategies.map((item) =>
                item.id === strategy.id ? strategy : item
              ),
            },
          })
        }
      />
    </form>
  )
}
