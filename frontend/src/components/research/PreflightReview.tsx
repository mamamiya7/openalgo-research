import type { ResearchConfig, Submission } from '@/api/scannerResearch'

export function settingValue(key: string, item: unknown): string {
  if (item === null || item === undefined) return 'Not recorded'
  if (Array.isArray(item))
    return (
      item
        .map((entry) => (entry === 'Bypass' ? 'All scanner signals' : String(entry)))
        .join(' + ') || 'None'
    )
  if (typeof item === 'boolean') return item ? 'Enabled' : 'Disabled'
  const terms: Record<string, string> = {
    csv: 'CSV order',
    reversed: 'Reversed CSV order',
    alphabetical: 'Alphabetical',
    shuffle: 'Seeded shuffle',
    strict: 'Require full planned allocation',
    remaining: 'Use available remaining allocation',
    balance: 'Net return minus maximum drawdown',
    return: 'Net marked return',
    drawdown: 'Lowest maximum drawdown',
    Bypass: 'All scanner signals',
    intraday: 'Within one trading day',
    multiday: 'Across trading days',
  }
  if (typeof item === 'number')
    return `${['initial_capital'].includes(key) ? 'INR ' : ''}${item.toLocaleString('en-IN', { maximumFractionDigits: 4 })}${key.endsWith('_pct') ? '%' : key.endsWith('_bps') ? ' basis points' : key === 'hold_sessions' ? ' exchange sessions' : ''}`
  return terms[String(item)] || String(item)
}
const names: Record<string, string> = {
  initial_capital: 'Starting cash',
  order_size_pct: 'Allocation per signal',
  target_pct: 'Profit target',
  stop_pct: 'Stop loss',
  hold_sessions: 'Maximum holding period',
  trade_horizon: 'Holding period',
  hold_minutes: 'Maximum holding minutes',
  entry_time: 'Enter no earlier than (IST)',
  exit_time: 'Exit by (IST)',
  cost_bps: 'Costs per side',
  slippage_bps: 'Slippage per side',
  trailing_pct: 'Trailing distance',
  trailing_enabled: 'Trailing stop',
  modes: 'Scanner-count triggers',
  entry_priority: 'Entry priority',
  priority_seed: 'Shuffle seed',
  max_exposure_pct: 'Maximum exposure',
  exposure_fill_mode: 'Allocation fill policy',
}
export function ConfigSummary({
  config,
  hideInactive = false,
}: {
  config: ResearchConfig | Record<string, unknown>
  hideInactive?: boolean
}) {
  return (
    <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {Object.entries(config)
        .filter(
          ([key, item]) =>
            !hideInactive ||
            !(
              (key === 'priority_seed' && config.entry_priority !== 'shuffle') ||
              (key === 'trailing_pct' && !config.trailing_enabled) ||
              (key === 'hold_sessions' && config.trade_horizon === 'intraday') ||
              (['hold_minutes', 'entry_time', 'exit_time'].includes(key) && item == null)
            )
        )
        .map(([key, item]) => (
          <div key={key} className="min-w-0">
            <dt className="text-xs text-muted-foreground">
              {names[key] || key.replaceAll('_', ' ')}
            </dt>
            <dd className="break-words text-sm">{settingValue(key, item)}</dd>
          </div>
        ))}
    </dl>
  )
}
export function PreflightReview({
  receipt,
  payload,
}: {
  receipt: Record<string, unknown>
  payload: Submission
}) {
  const spec = (receipt.specification || payload.specification) as Record<string, unknown>
  const windows = Array.isArray(receipt.windows)
    ? (receipt.windows as Array<Record<string, unknown>>)
    : []
  const variants = (Array.isArray(spec.variants) ? spec.variants : []) as Array<{
    name: string
    changes: Record<string, unknown>
  }>
  const search = (payload.kind === 'optimize' ? spec : spec.search) as
    | Record<string, unknown>
    | undefined
  return (
    <div className="space-y-5">
      <h3 className="font-semibold">Preflight receipt</h3>
      <p className="text-sm">
        {payload.kind === 'research'
          ? spec.intent === 'select_earlier'
            ? 'Select using earlier observations only, lock the selected setup, then evaluate each later window.'
            : 'Keep this exact supplied setup fixed for the later evaluation; no earlier selection is performed.'
          : payload.kind === 'optimize'
            ? 'Explore the recorded boundaries within the stated budget. Historical ranking is not an independent holdout.'
            : payload.kind === 'sensitivity'
              ? 'Change each stated assumption around the fixed baseline; no replacement winner is selected.'
              : 'Evaluate one fixed historical setup.'}
      </p>
      <h4 className="font-medium">Complete fixed assumptions</h4>
      <ConfigSummary config={(receipt.config || payload.config) as ResearchConfig} />
      <p className="text-sm">
        Planned evaluations: {settingValue('', receipt.planned_evaluations ?? 1)}
        {receipt.grid_count != null ? ` · Total grid: ${receipt.grid_count}` : ''}
        {receipt.previously_tested != null
          ? ` · Previously tested: ${receipt.previously_tested}`
          : ''}
      </p>
      {typeof receipt.interpretation === 'string' && (
        <p className="text-sm">{receipt.interpretation}</p>
      )}
      {windows.map((window, index) => (
        <section
          key={index}
          aria-label={`Preflight window ${index + 1}`}
          className="space-y-3 rounded-md border p-3"
        >
          <h4 className="font-semibold">Window {(window.fold as number) || index + 1}</h4>
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <h5 className="text-sm font-medium">Earlier observations</h5>
              <p className="text-sm">
                {settingValue('', window.train_from)} to {settingValue('', window.train_end)}
              </p>
              <p className="text-sm">
                {settingValue('', window.train_sessions)} exchange sessions ·{' '}
                {settingValue('', window.training_signals)} signals
              </p>
            </div>
            <div>
              <h5 className="text-sm font-medium">Excluded gap</h5>
              <p className="text-sm">
                {Number(window.gap_sessions) === 0
                  ? 'No gap'
                  : `${settingValue('', window.gap_from)} to ${settingValue('', window.gap_to)}`}
              </p>
              <p className="text-sm">{settingValue('', window.gap_sessions)} exchange sessions</p>
            </div>
            <div>
              <h5 className="text-sm font-medium">Later evaluation</h5>
              <p className="text-sm">
                {settingValue('', window.test_from)} to {settingValue('', window.test_end)}
              </p>
              <p className="text-sm">
                {settingValue('', window.test_sessions)} exchange sessions · at most{' '}
                {settingValue('', window.possible_entries)} possible next-session entries
              </p>
            </div>
          </div>
        </section>
      ))}
      {payload.kind === 'research' && (
        <p className="text-sm">
          Prior exploration declared: {spec.prior_explored ? 'Yes' : 'No'}.{' '}
          {spec.intent === 'select_earlier'
            ? `Selection requires at least ${spec.min_train_closed} earlier completed trades.`
            : 'The supplied setup is not rejected by a training-selection minimum.'}{' '}
          Each fold starts with fresh capital.
        </p>
      )}
      {search && (
        <section className="space-y-3">
          <h4 className="font-medium">Search boundaries and ranking</h4>
          <p className="text-sm">
            Approach: {String(search.mode)} · Budget: {String(search.budget)} · Ranking:{' '}
            {typeof receipt.ranking_definition === 'string'
              ? receipt.ranking_definition
              : settingValue('', search.rank_by)}
          </p>
          {search.axes != null &&
            typeof search.axes === 'object' &&
            Object.entries(search.axes).map(([key, axis]) => {
              const range = axis as Record<string, number>
              return (
                <p className="text-sm" key={key}>
                  {names[key] || key.replaceAll('_', ' ')}: {range.min} to {range.max}, step{' '}
                  {range.step}
                  {key === 'hold_sessions' ? ' exchange sessions' : '%'}
                </p>
              )
            })}
          <p className="text-sm">
            Trigger choices:{' '}
            {((search.mode_strategies as string[][]) || [])
              .map((group) => group.join(' + '))
              .join('; ')}
          </p>
          <p className="text-sm">
            Trailing choices:{' '}
            {Array.isArray(search.trailing_choices)
              ? (search.trailing_choices as Array<{ enabled: boolean; pct: number }>)
                  .map((choice) => `${choice.enabled ? 'Enabled' : 'Disabled'} at ${choice.pct}%`)
                  .join('; ')
              : 'Derived from the recorded trailing boundaries and explicit baseline state.'}
          </p>
        </section>
      )}
      {(payload.kind === 'research' || payload.kind === 'sensitivity') && (
        <section className="space-y-3">
          <h4 className="font-medium">Sensitivity cases</h4>
          {variants.length ? (
            variants.map((variant) => (
              <div key={variant.name}>
                <h5 className="text-sm font-medium">{variant.name}</h5>
                <ConfigSummary config={variant.changes} />
              </div>
            ))
          ) : (
            <p className="text-sm">
              Default cases: double costs ({Math.min(500, payload.config.cost_bps * 2)} basis
              points), higher slippage ({Math.min(500, payload.config.slippage_bps + 10)} basis
              points), and reversed CSV priority.
            </p>
          )}
        </section>
      )}
      <p className="text-xs text-muted-foreground">
        Possible entries are upper bounds, not promised completed trades. These assumptions are all
        available before submission; deep source lineage remains separately recorded.
      </p>
    </div>
  )
}
