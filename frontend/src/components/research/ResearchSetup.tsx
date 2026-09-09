import type { ResearchConfig } from '@/api/scannerResearch'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { type Draft, strategies } from '@/lib/researchDraft'

type NumberKey = Exclude<
  {
    [K in keyof ResearchConfig]: ResearchConfig[K] extends number ? K : never
  }[keyof ResearchConfig],
  undefined
>
const fields: Array<{
  key: NumberKey
  label: string
  min: number
  max: number
  integer?: boolean
}> = [
  { key: 'initial_capital', label: 'Starting cash (INR)', min: 1, max: 1e9 },
  { key: 'order_size_pct', label: 'Allocation per signal (%)', min: 0.01, max: 100 },
  { key: 'target_pct', label: 'Profit target (%)', min: 0.01, max: 500 },
  { key: 'stop_pct', label: 'Stop loss (%)', min: 0.01, max: 99 },
  { key: 'hold_sessions', label: 'Maximum holding sessions', min: 1, max: 252, integer: true },
  { key: 'cost_bps', label: 'Costs per side (basis points)', min: 0, max: 500 },
  { key: 'slippage_bps', label: 'Slippage per side (basis points)', min: 0, max: 500 },
  { key: 'trailing_pct', label: 'Trailing stop (%)', min: 0, max: 99 },
  { key: 'max_exposure_pct', label: 'Maximum exposure (%)', min: 0, max: 100 },
  { key: 'priority_seed', label: 'Shuffle seed', min: -2147483648, max: 2147483647, integer: true },
]
const selectClass = 'w-full min-w-0 rounded-md border bg-background p-2 text-sm'
const variantFields = [
  'cost_bps',
  'slippage_bps',
  'entry_priority',
  'priority_seed',
  'target_pct',
  'stop_pct',
  'hold_sessions',
] as const
export function ResearchSetup({
  draft,
  onChange,
}: {
  draft: Draft
  onChange: (draft: Draft) => void
}) {
  const update = (changes: Partial<ResearchConfig>) =>
    onChange({ ...draft, config: { ...draft.config, ...changes } })
  const research = (changes: Partial<Draft['research']>) =>
    onChange({ ...draft, research: { ...draft.research, ...changes } })
  const search = (changes: Partial<Draft['search']>) => {
    const updated = { ...draft.search, ...changes }
    if (changes.exclude_indices?.length === 0) delete updated.parent_job_id
    if (changes.axes?.trailing_pct) delete updated.trailing_choices
    onChange({ ...draft, search: updated })
  }
  const searchVisible =
    draft.kind === 'optimize' ||
    (draft.kind === 'research' && draft.research.intent === 'select_earlier')
  const intraday = draft.config.trade_horizon === 'intraday'
  const vectorbt = draft.execution?.engine === 'vectorbt'
  const optuna = draft.kind === 'optimize' && (draft.execution?.optimizer === 'optuna' || vectorbt)
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <Label htmlFor="experiment-kind">Run type</Label>
        <select
          id="experiment-kind"
          className={selectClass}
          value={draft.kind}
          onChange={(e) =>
            onChange({
              ...draft,
              kind: e.target.value as Draft['kind'],
              ...(vectorbt && e.target.value === 'optimize'
                ? { execution: { ...draft.execution!, optimizer: 'optuna' } }
                : {}),
            })
          }
        >
          <option value="backtest">Backtest one fixed setup</option>
          <option value="optimize">Explore settings</option>
          {!vectorbt && !optuna && <option value="research">Test on later periods</option>}
          {!vectorbt && !optuna && <option value="sensitivity">Check sensitivity</option>}
        </select>
      </div>
      <details open={draft.kind === 'backtest'}>
        <summary className="cursor-pointer font-medium">Trade settings</summary>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {fields.slice(0, 4).map((field) => (
            <div key={field.key} className="min-w-0 space-y-2">
              <Label htmlFor={field.key}>{field.label}</Label>
              <Input
                id={field.key}
                type="number"
                required
                min={field.min}
                max={field.max}
                step={field.integer ? 1 : 'any'}
                value={Number.isNaN(draft.config[field.key]) ? '' : draft.config[field.key]}
                onChange={(e) => update({ [field.key]: e.target.valueAsNumber })}
              />
            </div>
          ))}
          {!vectorbt && (
            <div className="min-w-0 space-y-2">
              <Label htmlFor="trade-horizon">Holding period</Label>
              <select
                id="trade-horizon"
                className={selectClass}
                value={draft.config.trade_horizon || 'multiday'}
                onChange={(e) => {
                  const timed = e.target.value === 'intraday'
                  const axes = { ...draft.search.axes }
                  delete axes.hold_minutes
                  delete axes.hold_sessions
                  axes[timed ? 'hold_minutes' : 'hold_sessions'] = timed
                    ? { min: 15, max: 120, step: 15 }
                    : { min: 3, max: 7, step: 2 }
                  onChange({
                    ...draft,
                    config: {
                      ...draft.config,
                      trade_horizon: timed ? 'intraday' : 'multiday',
                      hold_minutes: timed ? 60 : null,
                    },
                    search: {
                      ...draft.search,
                      axes,
                      hold_axis: timed ? 'hold_minutes' : 'hold_sessions',
                      exclude_indices: [],
                      parent_job_id: undefined,
                    },
                  })
                }}
              >
                <option value="multiday">Across trading days</option>
                <option value="intraday">Within one trading day</option>
              </select>
            </div>
          )}
          <div className="min-w-0 space-y-2">
            <Label htmlFor="maximum-hold">
              {intraday ? 'Maximum holding minutes' : 'Maximum holding sessions'}
            </Label>
            <Input
              id="maximum-hold"
              type="number"
              required
              min={1}
              max={intraday ? 100000 : 252}
              step={1}
              value={intraday ? (draft.config.hold_minutes ?? 60) : draft.config.hold_sessions}
              onChange={(e) =>
                update(
                  intraday
                    ? { hold_minutes: e.target.valueAsNumber }
                    : { hold_sessions: e.target.valueAsNumber }
                )
              }
            />
          </div>
        </div>
        <details className="mt-4">
          <summary className="cursor-pointer text-sm text-muted-foreground">
            Advanced settings
          </summary>{' '}
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {(!vectorbt ? (['entry_time', 'exit_time'] as const) : []).map((key) => (
              <div key={key} className="space-y-2">
                <Label htmlFor={key}>
                  {key === 'entry_time' ? 'Enter no earlier than (IST)' : 'Exit by (IST)'}
                </Label>
                <Input
                  id={key}
                  type="time"
                  value={draft.config[key] || ''}
                  onChange={(e) => update({ [key]: e.target.value || null })}
                />
              </div>
            ))}
            {!intraday && !vectorbt && (
              <div className="space-y-2">
                <Label htmlFor="hold-minutes">Optional time limit (minutes)</Label>
                <Input
                  id="hold-minutes"
                  type="number"
                  min={1}
                  max={100000}
                  step={1}
                  value={draft.config.hold_minutes ?? ''}
                  onChange={(e) =>
                    update({ hold_minutes: e.target.value === '' ? null : e.target.valueAsNumber })
                  }
                />
              </div>
            )}
            {fields
              .slice(5)
              .filter(
                (field) => !vectorbt || !['max_exposure_pct', 'priority_seed'].includes(field.key)
              )
              .map((field) => (
                <div key={field.key} className="min-w-0 space-y-2">
                  <Label htmlFor={field.key}>{field.label}</Label>
                  <Input
                    id={field.key}
                    type="number"
                    required
                    min={field.min}
                    max={field.max}
                    step={field.integer ? 1 : 'any'}
                    value={Number.isNaN(draft.config[field.key]) ? '' : draft.config[field.key]}
                    onChange={(e) => update({ [field.key]: e.target.valueAsNumber })}
                  />
                </div>
              ))}
          </div>
          <div className="mt-4 space-y-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft.config.trailing_enabled}
                onChange={(e) => update({ trailing_enabled: e.target.checked })}
              />
              Enable trailing stop
            </label>
            {!vectorbt && (
              <div className="space-y-2">
                <Label htmlFor="trigger-modes">Scanner-count trigger</Label>
                <select
                  id="trigger-modes"
                  className={selectClass}
                  value={draft.config.modes.join('|')}
                  onChange={(e) => update({ modes: e.target.value.split('|') })}
                >
                  {strategies.map((modes) => (
                    <option key={modes.join('|')} value={modes.join('|')}>
                      {modes.join(' + ')}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-muted-foreground">
                  Bypass takes all signals. Other filters use prior scanner session counts; combined
                  filters accept any selected trigger.
                </p>
              </div>
            )}
            {!vectorbt && (
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="entry-priority">Entry priority</Label>
                  <select
                    id="entry-priority"
                    className={selectClass}
                    value={draft.config.entry_priority}
                    onChange={(e) =>
                      update({ entry_priority: e.target.value as ResearchConfig['entry_priority'] })
                    }
                  >
                    <option value="csv">CSV order</option>
                    <option value="alphabetical">Alphabetical</option>
                    <option value="reversed">Reversed CSV</option>
                    <option value="shuffle">Seeded shuffle</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="fill-policy">Exposure and cash fill policy</Label>
                  <select
                    id="fill-policy"
                    className={selectClass}
                    value={draft.config.exposure_fill_mode}
                    onChange={(e) =>
                      update({
                        exposure_fill_mode: e.target.value as ResearchConfig['exposure_fill_mode'],
                      })
                    }
                  >
                    <option value="strict">Strict full budget</option>
                    <option value="remaining">Use remaining budget</option>
                  </select>
                </div>
              </div>
            )}
            {vectorbt && (
              <p className="text-xs text-muted-foreground">
                Daily prices. Entries use the next session’s open in CSV order with shared cash.
                Protective exits start one session after entry. Orders need their full allocation.
              </p>
            )}
          </div>
        </details>
      </details>
      {draft.kind === 'research' && (
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="research-intent">Research intent</Label>
            <select
              id="research-intent"
              className={selectClass}
              value={draft.research.intent}
              onChange={(e) => research({ intent: e.target.value as Draft['research']['intent'] })}
            >
              <option value="fixed_setup">Test this exact supplied setup later</option>
              <option value="select_earlier">Select earlier, then lock and test later</option>
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="research-scheme">Chronological scheme</Label>
            <select
              id="research-scheme"
              className={selectClass}
              value={draft.research.scheme}
              onChange={(e) =>
                research({
                  scheme: e.target.value as Draft['research']['scheme'],
                  folds: e.target.value === 'holdout' ? 1 : 3,
                })
              }
            >
              <option value="holdout">One later holdout</option>
              <option value="walk_forward">Expanding walk-forward</option>
            </select>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {(['train_end', 'test_end'] as const).map((key) => (
              <div key={key} className="space-y-2">
                <Label htmlFor={key}>
                  {key === 'train_end' ? 'Earlier period cutoff' : 'Final later-period cutoff'}
                </Label>
                <Input
                  id={key}
                  type="date"
                  required
                  value={draft.research[key]}
                  onChange={(e) => research({ [key]: e.target.value })}
                />
              </div>
            ))}
            {(['gap_sessions', 'folds', 'min_train_closed'] as const)
              .filter(
                (key) => key !== 'min_train_closed' || draft.research.intent === 'select_earlier'
              )
              .map((key) => (
                <div key={key} className="space-y-2">
                  <Label htmlFor={key}>
                    {key === 'gap_sessions'
                      ? 'Gap (exchange sessions)'
                      : key === 'folds'
                        ? 'Later windows'
                        : 'Minimum earlier closed trades for selection'}
                  </Label>
                  <Input
                    id={key}
                    type="number"
                    required
                    min={key === 'folds' ? 1 : 0}
                    max={key === 'folds' ? 5 : key === 'gap_sessions' ? 252 : 25000}
                    step={1}
                    disabled={key === 'folds' && draft.research.scheme === 'holdout'}
                    value={draft.research[key]}
                    onChange={(e) => research({ [key]: e.target.valueAsNumber })}
                  />
                </div>
              ))}
          </div>
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.research.prior_explored}
              onChange={(e) => research({ prior_explored: e.target.checked })}
            />
            I have already explored these observations
          </label>
          <p className="text-xs text-muted-foreground">
            Each later window starts with fresh capital. Known overlap remains recorded even if this
            declaration is unchecked. A dated CSV cannot establish an unbiased scanner universe.
          </p>
        </div>
      )}
      {searchVisible && (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="search-mode">Search approach</Label>
              <select
                id="search-mode"
                className={selectClass}
                value={draft.search.mode}
                onChange={(e) => search({ mode: e.target.value as Draft['search']['mode'] })}
              >
                <option value="quick">Quick scan</option>
                <option value="full">Full scan within budget</option>
                <option value="exhaustive">Exhaustive remaining grid</option>
                <option value="auto">
                  {optuna ? 'Sample within budget' : 'Auto: broad then nearby settings'}
                </option>
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="search-budget">Evaluation budget</Label>
              <Input
                id="search-budget"
                type="number"
                required
                min={1}
                max={draft.kind === 'research' ? 1000 : 5000}
                step={1}
                value={draft.search.budget}
                onChange={(e) => search({ budget: e.target.valueAsNumber })}
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {optuna
              ? 'Optuna samples distinct settings from these boundaries. Exhaustive tests the full grid.'
              : 'Quick/full sample evenly within the budget; Auto reserves work for nearby settings. Exhaustive must cover every remaining setting.'}
          </p>
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium">Search boundaries</legend>
            {!vectorbt && (
              <div className="space-y-2">
                <Label htmlFor="holding-search-unit">Search holding period in</Label>
                <select
                  id="holding-search-unit"
                  className={selectClass}
                  value={draft.search.hold_axis || 'hold_sessions'}
                  onChange={(e) => {
                    const key = e.target.value as 'hold_minutes' | 'hold_sessions'
                    const axes = { ...draft.search.axes }
                    delete axes.hold_minutes
                    delete axes.hold_sessions
                    axes[key] =
                      key === 'hold_minutes'
                        ? { min: 15, max: 120, step: 15 }
                        : { min: 3, max: 7, step: 2 }
                    search({ axes, hold_axis: key, exclude_indices: [] })
                  }}
                >
                  <option value="hold_sessions">Trading sessions</option>
                  <option value="hold_minutes">Minutes</option>
                </select>
              </div>
            )}
            {Object.entries(draft.search.axes).map(([key, axis]) => (
              <div key={key} className="space-y-2">
                <p className="text-sm">{key.replaceAll('_', ' ')}</p>
                <div className="grid grid-cols-3 gap-2">
                  {(['min', 'max', 'step'] as const).map((part) => (
                    <div key={part}>
                      <Label htmlFor={`${key}-${part}`} className="text-xs capitalize">
                        {part}
                      </Label>
                      <Input
                        id={`${key}-${part}`}
                        aria-label={`${key} ${part}`}
                        type="number"
                        required
                        step="any"
                        value={axis[part]}
                        onChange={(e) =>
                          search({
                            axes: {
                              ...draft.search.axes,
                              [key]: { ...axis, [part]: e.target.valueAsNumber },
                            },
                            exclude_indices: [],
                          })
                        }
                      />
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </fieldset>
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.search.include_trailing_off}
              onChange={(e) =>
                search({ include_trailing_off: e.target.checked, exclude_indices: [] })
              }
            />
            {vectorbt
              ? 'Also test without a trailing stop'
              : 'Include explicit trailing-off baseline'}
          </label>
          {(!vectorbt || draft.search.trailing_choices) && (
            <details>
              <summary className="cursor-pointer font-medium">Explicit trailing states</summary>
              <p className="my-3 text-xs text-muted-foreground">
                Use explicit pairs to preserve enabled-at-zero or disabled-with-positive-distance
                identities. When present, these replace the trailing axis; including off adds a
                separate disabled-at-zero baseline.
              </p>
              <label className="flex gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={!!draft.search.trailing_choices}
                  onChange={(event) =>
                    search({
                      trailing_choices: event.target.checked
                        ? [
                            {
                              enabled: draft.config.trailing_enabled,
                              pct: draft.config.trailing_pct,
                            },
                          ]
                        : undefined,
                      exclude_indices: [],
                    })
                  }
                />
                Use explicit trailing state/value choices
              </label>
              {draft.search.trailing_choices?.map((choice, index) => (
                <div key={index} className="my-3 flex flex-wrap items-center gap-3">
                  <label className="flex gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={choice.enabled}
                      onChange={(event) =>
                        search({
                          trailing_choices: draft.search.trailing_choices?.map((item, i) =>
                            i === index ? { ...item, enabled: event.target.checked } : item
                          ),
                          exclude_indices: [],
                        })
                      }
                    />
                    Choice {index + 1} enabled
                  </label>
                  <Input
                    className="w-24"
                    aria-label={`Trailing choice ${index + 1} percent`}
                    type="number"
                    min={0}
                    max={99}
                    step="any"
                    value={choice.pct}
                    onChange={(event) =>
                      search({
                        trailing_choices: draft.search.trailing_choices?.map((item, i) =>
                          i === index ? { ...item, pct: event.target.valueAsNumber } : item
                        ),
                        exclude_indices: [],
                      })
                    }
                  />
                  <Button
                    type="button"
                    variant="outline"
                    disabled={draft.search.trailing_choices?.length === 1}
                    onClick={() =>
                      search({
                        trailing_choices: draft.search.trailing_choices?.filter(
                          (_, i) => i !== index
                        ),
                        exclude_indices: [],
                      })
                    }
                  >
                    Remove choice
                  </Button>
                </div>
              ))}
              {draft.search.trailing_choices && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={draft.search.trailing_choices.length >= 12}
                  onClick={() =>
                    search({
                      trailing_choices: [
                        ...(draft.search.trailing_choices || []),
                        { enabled: true, pct: 2 },
                      ],
                      exclude_indices: [],
                    })
                  }
                >
                  Add trailing choice
                </Button>
              )}
            </details>
          )}
          {!vectorbt && (
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">
                Meaningful trigger strategies (up to eight)
              </legend>
              {strategies.map((group) => (
                <label key={group.join('|')} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={draft.search.mode_strategies.some(
                      (item) => item.join('|') === group.join('|')
                    )}
                    onChange={(e) =>
                      search({
                        mode_strategies: e.target.checked
                          ? [...draft.search.mode_strategies, group]
                          : draft.search.mode_strategies.filter(
                              (item) => item.join('|') !== group.join('|')
                            ),
                        exclude_indices: [],
                      })
                    }
                  />
                  {group.join(' + ')}
                </label>
              ))}
            </fieldset>
          )}
          <div className="space-y-2">
            <Label htmlFor="ranking">Ranking focus</Label>
            <select
              id="ranking"
              className={selectClass}
              value={draft.search.rank_by}
              onChange={(e) => search({ rank_by: e.target.value as Draft['search']['rank_by'] })}
            >
              <option value="balance">Balance: return minus drawdown</option>
              <option value="return">Net marked return</option>
              <option value="drawdown">Lowest drawdown</option>
            </select>
          </div>
        </div>
      )}
      {(draft.kind === 'research' || draft.kind === 'sensitivity') && (
        <details>
          <summary className="cursor-pointer font-medium">Sensitivity assumptions</summary>
          <p className="my-3 text-sm text-muted-foreground">
            With no custom variants, test double fees, 10 bps additional slippage and reversed
            priority. Sensitivity never selects a replacement winner.
          </p>
          {draft.variants.map((variant, index) => {
            const changeKey = (Object.keys(variant.changes)[0] ||
              'cost_bps') as (typeof variantFields)[number]
            const change = (changes: Partial<ResearchConfig>) =>
              onChange({
                ...draft,
                variants: draft.variants.map((item, i) =>
                  i === index ? { ...item, changes } : item
                ),
              })
            return (
              <div key={index} className="my-3 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                <Input
                  aria-label={`Variant ${index + 1} name`}
                  value={variant.name}
                  onChange={(e) =>
                    onChange({
                      ...draft,
                      variants: draft.variants.map((item, i) =>
                        i === index ? { ...item, name: e.target.value } : item
                      ),
                    })
                  }
                />
                <select
                  aria-label={`Variant ${index + 1} assumption`}
                  className={selectClass}
                  value={changeKey}
                  onChange={(e) => {
                    const key = e.target.value as (typeof variantFields)[number]
                    change({ [key]: draft.config[key] })
                  }}
                >
                  {variantFields.map((key) => (
                    <option key={key} value={key}>
                      {key.replaceAll('_', ' ')}
                    </option>
                  ))}
                </select>
                {changeKey === 'entry_priority' ? (
                  <select
                    aria-label={`Variant ${index + 1} priority`}
                    className={selectClass}
                    value={variant.changes.entry_priority}
                    onChange={(e) =>
                      change({ entry_priority: e.target.value as ResearchConfig['entry_priority'] })
                    }
                  >
                    <option value="csv">CSV order</option>
                    <option value="alphabetical">Alphabetical</option>
                    <option value="reversed">Reversed CSV</option>
                    <option value="shuffle">Seeded shuffle</option>
                  </select>
                ) : (
                  <Input
                    aria-label={`Variant ${index + 1} value`}
                    type="number"
                    step="any"
                    required
                    value={variant.changes[changeKey] ?? 0}
                    onChange={(e) => change({ [changeKey]: e.target.valueAsNumber })}
                  />
                )}
                <Button
                  type="button"
                  variant="outline"
                  onClick={() =>
                    onChange({ ...draft, variants: draft.variants.filter((_, i) => i !== index) })
                  }
                >
                  Remove
                </Button>
              </div>
            )
          })}
          <Button
            type="button"
            variant="outline"
            disabled={draft.variants.length >= 5}
            onClick={() =>
              onChange({
                ...draft,
                variants: [
                  ...draft.variants,
                  { name: `Cost scenario ${draft.variants.length + 1}`, changes: { cost_bps: 20 } },
                ],
              })
            }
          >
            Add sensitivity scenario
          </Button>
        </details>
      )}
    </div>
  )
}
