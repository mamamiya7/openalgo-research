import type { PortfolioAxis, PortfolioRange, PortfolioStrategy } from '@/api/portfolioResearch'
import type { ResearchSource } from '@/api/scannerResearch'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'

const selectClass = 'h-10 w-full rounded-md border border-input bg-background px-3 text-sm'
const limits: Record<PortfolioAxis, [number, number]> = {
  target_pct: [0.01, 500],
  stop_pct: [0.01, 99],
  hold_sessions: [1, 252],
  hold_minutes: [1, 100000],
  trailing_pct: [0.01, 99],
  order_size_pct: [0.01, 100],
  allocation_pct: [0, 100],
}
const labels: Record<PortfolioAxis, string> = {
  target_pct: 'Profit target (%)',
  stop_pct: 'Stop loss (%)',
  hold_sessions: 'Maximum holding sessions',
  hold_minutes: 'Maximum holding minutes',
  trailing_pct: 'Trailing stop (%)',
  order_size_pct: 'Per-trade size (%)',
  allocation_pct: 'Portfolio allocation (%)',
}
export function portfolioSearchRange(field: PortfolioAxis, value: number): PortfolioRange {
  if (field === 'allocation_pct') return { min: 0, max: 100, step: 25 }
  const integer = field === 'hold_sessions' || field === 'hold_minutes'
  const step = integer
    ? Math.max(1, Math.floor(value / 2))
    : Math.max(0.01, Math.round(value * 50) / 100)
  const min = Math.max(limits[field][0], value - step)
  const max = min + Math.floor((Math.min(limits[field][1], value + step) - min) / step) * step
  return { min: Number(min.toFixed(6)), max: Number(max.toFixed(6)), step }
}
export function PortfolioStrategySettings({
  strategy,
  source,
  optimizing,
  onChange,
  onClose,
  onRestoreFocus,
}: {
  strategy: PortfolioStrategy | null
  source?: ResearchSource
  optimizing: boolean
  onChange: (strategy: PortfolioStrategy, allocationChanged?: boolean) => void
  onClose: () => void
  onRestoreFocus?: () => void
}) {
  if (!strategy) return null
  const current = strategy
  const config = current.config
  const minuteHold = config.hold_minutes != null
  const intraday = config.trade_horizon === 'intraday'
  const updateConfig = (patch: Partial<typeof config>) =>
    onChange({ ...current, config: { ...config, ...patch } })
  const changeHold = (withinDay: boolean, minutes: boolean) => {
    onChange({
      ...current,
      config: {
        ...config,
        trade_horizon: withinDay ? 'intraday' : 'multiday',
        hold_minutes: minutes ? (config.hold_minutes ?? 60) : null,
      },
    })
  }
  function parameter(field: PortfolioAxis) {
    const range = optimizing ? current.search[field] : undefined
    const value = field === 'allocation_pct' ? current.allocation_pct : Number(config[field])
    const setValue = (next: number) =>
      field === 'allocation_pct'
        ? onChange({ ...current, allocation_pct: next }, true)
        : updateConfig({ [field]: next })
    const changeRange = (part: keyof PortfolioRange, next: number) => {
      if (range)
        onChange({ ...current, search: { ...current.search, [field]: { ...range, [part]: next } } })
    }
    return (
      <div key={field} className="space-y-2 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Label htmlFor={`portfolio-${field}`}>{labels[field]}</Label>
          {optimizing && (
            <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
              <input
                className="h-4 w-4 accent-primary"
                type="checkbox"
                aria-label={`Optimize ${labels[field]}`}
                checked={Boolean(range)}
                onChange={(event) => {
                  const search = { ...current.search }
                  if (event.target.checked) search[field] = portfolioSearchRange(field, value)
                  else delete search[field]
                  onChange({ ...current, search })
                }}
              />
              Optimize
            </label>
          )}
        </div>
        {range ? (
          <div className="grid grid-cols-3 gap-2">
            {(['min', 'max', 'step'] as const).map((part) => (
              <div key={part} className="space-y-1">
                <Label className="text-xs text-muted-foreground" htmlFor={`${field}-${part}`}>
                  {part === 'min' ? 'From' : part === 'max' ? 'To' : 'Step'}
                </Label>
                <Input
                  id={`${field}-${part}`}
                  aria-label={`${labels[field]} ${part}`}
                  type="number"
                  value={Number.isFinite(range[part]) ? range[part] : ''}
                  min={part === 'step' ? 0.000001 : limits[field][0]}
                  max={limits[field][1]}
                  step={field.startsWith('hold_') ? 1 : 'any'}
                  onChange={(event) => changeRange(part, event.target.valueAsNumber)}
                />
              </div>
            ))}
          </div>
        ) : (
          <Input
            id={`portfolio-${field}`}
            type="number"
            value={Number.isFinite(value) ? value : ''}
            min={limits[field][0]}
            max={limits[field][1]}
            step={field.startsWith('hold_') ? 1 : 'any'}
            onChange={(event) => setValue(event.target.valueAsNumber)}
          />
        )}
        {field === 'order_size_pct' && (
          <p className="text-xs text-muted-foreground">Of this strategy’s allocation.</p>
        )}
      </div>
    )
  }
  return (
    <Sheet
      open={Boolean(strategy)}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <SheetContent
        className="w-full overflow-y-auto sm:max-w-lg motion-reduce:animate-none motion-reduce:transition-none"
        onCloseAutoFocus={(event) => {
          if (onRestoreFocus) {
            event.preventDefault()
            onRestoreFocus()
          }
        }}
      >
        <SheetHeader className="border-b px-6 py-5">
          <SheetTitle>{current.name} settings</SheetTitle>
          <SheetDescription>
            {source?.receipt.signal_count.toLocaleString('en-IN') ?? 'Saved'} signals
          </SheetDescription>
        </SheetHeader>
        <div className="divide-y px-6">
          {parameter('allocation_pct')}
          {parameter('order_size_pct')}
          {parameter('target_pct')}
          {parameter('stop_pct')}
          <div className="space-y-3 py-4">
            <Label htmlFor="portfolio-horizon">Holding period</Label>
            <select
              id="portfolio-horizon"
              className={selectClass}
              value={intraday ? 'intraday' : 'multiday'}
              onChange={(event) =>
                changeHold(event.target.value === 'intraday', event.target.value === 'intraday')
              }
            >
              <option value="multiday">Across trading days</option>
              <option value="intraday">Close within the day</option>
            </select>
            {!intraday && (
              <select
                aria-label="Measure holding in"
                className={selectClass}
                value={minuteHold ? 'minutes' : 'sessions'}
                onChange={(event) => changeHold(false, event.target.value === 'minutes')}
              >
                <option value="sessions">Trading sessions</option>
                <option value="minutes">Minutes</option>
              </select>
            )}
            {parameter(minuteHold ? 'hold_minutes' : 'hold_sessions')}
          </div>
          <div className="py-4">
            <label className="flex cursor-pointer items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                className="h-4 w-4 accent-primary"
                checked={config.trailing_enabled}
                onChange={(event) => {
                  onChange({
                    ...current,
                    config: {
                      ...config,
                      trailing_enabled: event.target.checked,
                      trailing_pct: event.target.checked
                        ? config.trailing_pct || 2
                        : config.trailing_pct,
                    },
                  })
                }}
              />
              Trailing protection
            </label>
            {config.trailing_enabled && parameter('trailing_pct')}
          </div>
          <details className="py-4">
            <summary className="cursor-pointer text-sm font-medium">Entry & exit timing</summary>
            <div className="mt-3 grid grid-cols-2 gap-3">
              {(['entry_time', 'exit_time'] as const).map((field) => (
                <div key={field} className="space-y-2">
                  <Label htmlFor={`portfolio-${field}`}>
                    {field === 'entry_time' ? 'Enter after (IST)' : 'Exit by (IST)'}
                  </Label>
                  <Input
                    id={`portfolio-${field}`}
                    type="time"
                    value={config[field] ?? ''}
                    onChange={(event) => updateConfig({ [field]: event.target.value || null })}
                  />
                </div>
              ))}
            </div>
          </details>
          <details className="py-4">
            <summary className="cursor-pointer text-sm font-medium">
              Trading costs{' '}
              <span className="ml-2 font-normal text-muted-foreground">
                {config.cost_bps} bps + {config.slippage_bps} bps slippage
              </span>
            </summary>
            <div className="mt-3 grid grid-cols-2 gap-3">
              {(['cost_bps', 'slippage_bps'] as const).map((field) => (
                <div key={field} className="space-y-2">
                  <Label htmlFor={`portfolio-${field}`}>
                    {field === 'cost_bps' ? 'Fees per side (bps)' : 'Slippage per side (bps)'}
                  </Label>
                  <Input
                    id={`portfolio-${field}`}
                    type="number"
                    min={0}
                    max={500}
                    step="any"
                    value={Number.isFinite(config[field]) ? config[field] : ''}
                    onChange={(event) => updateConfig({ [field]: event.target.valueAsNumber })}
                  />
                </div>
              ))}
            </div>
          </details>
        </div>
        <SheetFooter className="px-6">
          <Button type="button" onClick={onClose}>
            Done
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
