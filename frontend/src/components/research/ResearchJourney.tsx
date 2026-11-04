import { Check, ChevronRight, Clock3, Database, FlaskConical, Play, Settings2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import {
  type JourneyPlan,
  type JourneyPlanUpdate,
  type JourneyState,
  researchJourney,
} from '@/api/researchJourney'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { researchError } from '@/hooks/useResearchExperiment'

const terminal = new Set(['complete', 'none', 'needs_action'])
const noneMessage = 'None of the tested setups passed validation.'
const number = (value: number) => value.toLocaleString('en-IN')
const money = (value: unknown) =>
  typeof value === 'number'
    ? `₹${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
    : '—'
const phaseLabel: Record<JourneyState['phase'], string> = {
  preparing: 'Preparing your data',
  review: 'Review your research plan',
  queued: 'Research queued',
  researching: 'Research in progress',
  finalizing: 'Saving and verifying the result',
  complete: 'Best passing result saved',
  none: noneMessage,
  needs_action: 'Research needs your attention',
}
const remedyLabel: Record<string, string> = {
  funding_and_size: 'Check capital and trade size',
  lower_turnover: 'Test holding and exit settings',
  temporal_stability: 'Check consistency over time',
  reduce_concentration: 'Check allocation',
  stop: 'Finish research',
}
function editable(plan: JourneyPlan): JourneyPlanUpdate {
  return { revision: plan.revision, style: plan.style, ...plan.settings }
}

/** A reviewed plan starts work; mounting and polling only prepare/read it. */
export function ResearchJourney({
  experimentId,
  revision,
  enabled,
  onSaved,
  onResult,
}: {
  experimentId: string
  revision: number
  enabled: boolean
  onSaved?: (value: JourneyState) => Promise<void> | void
  onResult?: (jobId: string) => Promise<void> | void
}) {
  const [value, setValue] = useState<JourneyState | null>(null)
  const [form, setForm] = useState<JourneyPlanUpdate | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<'save' | 'start' | null>(null)
  const [retry, setRetry] = useState(0)
  const formIdentity = useRef('')
  const action = useRef<AbortController | null>(null)
  const request = useRef<{ plan: string; id: string } | null>(null)
  const opened = useRef<string | null>(null)
  const saved = useRef(onSaved)
  const result = useRef(onResult)
  saved.current = onSaved
  result.current = onResult

  function accept(next: JourneyState) {
    setValue(next)
    setError(null)
    if (next.plan) {
      const key = `${next.plan.id}:${next.plan.revision}`
      if (key !== formIdentity.current) {
        formIdentity.current = key
        setForm(editable(next.plan))
      }
    }
  }
  // biome-ignore lint/correctness/useExhaustiveDependencies: Explicit retry repeats the same idempotent preparation; callbacks are refs to avoid restarting work.
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let failures = 0
    const next = (state: JourneyState) => {
      if (controller.signal.aborted) return
      failures = 0
      accept(state)
      if (state.revision !== revision && state.experiment) {
        void Promise.resolve(saved.current?.(state)).catch(fail)
      }
      if (!terminal.has(state.phase)) timer = setTimeout(poll, 2500)
    }
    const fail = (cause: unknown) => {
      if (controller.signal.aborted) return
      failures += 1
      if (failures <= 3) {
        timer = setTimeout(poll, 2500 * failures)
      } else setError(researchError(cause))
    }
    const poll = () => {
      researchJourney.get(experimentId, controller.signal).then(next).catch(fail)
    }
    researchJourney.prepare(experimentId, revision, controller.signal).then(next).catch(fail)
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [experimentId, revision, enabled, retry])

  useEffect(() => () => action.current?.abort(), [])
  useEffect(() => {
    if (value?.phase !== 'complete' || !value.result?.accepted || !value.result.job_id) return
    const id = value.result.job_id
    if (opened.current === id) return
    opened.current = id
    void Promise.resolve(result.current?.(id)).catch((cause: unknown) => {
      setError(researchError(cause))
    })
  }, [value])

  const plan = value?.plan
  const changed = !!plan && !!form && JSON.stringify(form) !== JSON.stringify(editable(plan))
  const running = ['queued', 'researching', 'finalizing'].includes(value?.phase ?? '')
  const stale = !enabled || (!!plan && plan.revision !== revision)
  const canEdit = !!plan && !!form && !running && !pending && enabled
  const start = async () => {
    if (!plan || !value?.can_start || changed || stale || action.current) return
    const controller = new AbortController()
    action.current = controller
    setPending('start')
    if (request.current?.plan !== plan.id)
      request.current = { plan: plan.id, id: crypto.randomUUID() }
    try {
      const next = await researchJourney.start(
        experimentId,
        { revision: plan.revision, plan_id: plan.id, request_id: request.current.id },
        controller.signal
      )
      if (controller.signal.aborted) return
      accept(next)
      await saved.current?.(next)
      if (!controller.signal.aborted) setRetry((attempt) => attempt + 1)
    } catch (cause) {
      if (!controller.signal.aborted) setError(researchError(cause))
    } finally {
      if (!controller.signal.aborted) setPending(null)
      if (action.current === controller) action.current = null
    }
  }
  const save = async () => {
    if (!form || !changed || !canEdit || action.current) return
    const controller = new AbortController()
    action.current = controller
    setPending('save')
    try {
      const next = await researchJourney.updatePlan(experimentId, form, controller.signal)
      if (controller.signal.aborted) return
      accept(next)
      await saved.current?.(next)
      if (!controller.signal.aborted) setRetry((attempt) => attempt + 1)
    } catch (cause) {
      if (!controller.signal.aborted) setError(researchError(cause))
    } finally {
      if (!controller.signal.aborted) setPending(null)
      if (action.current === controller) action.current = null
    }
  }
  const field = (key: keyof JourneyPlan['settings'], label: string, step: number) => (
    <div className="space-y-1.5" key={key}>
      <Label htmlFor={`journey-${experimentId}-${key}`} className="text-xs">
        {label}
      </Label>
      <Input
        id={`journey-${experimentId}-${key}`}
        type="number"
        min={key === 'capital' || key === 'hold_sessions' ? 1 : 0.01}
        step={step}
        value={form?.[key] ?? ''}
        disabled={!canEdit}
        onChange={(event) => {
          const amount = Number(event.target.value)
          setForm((current) => (current ? { ...current, [key]: amount } : current))
        }}
      />
    </div>
  )
  const latest = value?.decisions?.at(-1)
  const progress = running ? value?.run?.progress : value?.preparation?.progress
  const step =
    value?.phase === 'preparing' || !value ? 0 : value.phase === 'review' ? 1 : running ? 2 : 3

  return (
    <section
      aria-label="Automatic research journey"
      className="space-y-5 rounded-xl border p-4 sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Automatic research
          </p>
          <h2 className="mt-1 text-xl font-semibold" aria-live="polite">
            {value ? phaseLabel[value.phase] : 'Preparing your research plan'}
          </h2>
        </div>
        <ol
          aria-label="Research steps"
          className="flex items-center gap-2 text-xs text-muted-foreground"
        >
          {['Prepare', 'Plan', 'Research', 'Result'].map((label, index) => (
            <li
              key={label}
              className="flex items-center gap-2"
              aria-current={index === step ? 'step' : undefined}
            >
              <span className={index === step ? 'font-semibold text-foreground' : ''}>
                {index < step && <Check className="mr-1 inline size-3" />}
                {label}
              </span>
              {index < 3 && <ChevronRight className="size-3" aria-hidden="true" />}
            </li>
          ))}
        </ol>
      </div>
      {(error || value?.error) && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/30 p-3 text-sm"
        >
          <span>{error || value?.error}</span>
          <Button
            size="sm"
            variant="outline"
            disabled={!enabled || !!pending}
            onClick={() => setRetry((attempt) => attempt + 1)}
          >
            Retry safely
          </Button>
        </div>
      )}
      {value?.phase === 'review' && stale && (
        <output className="block text-sm text-muted-foreground">
          Save your setup changes to refresh this plan before starting.
        </output>
      )}
      {(running || value?.phase === 'preparing' || !value) && (
        <section aria-live="polite" className="space-y-3 rounded-lg bg-muted/40 p-4">
          <div className="flex items-start justify-between gap-3 text-sm">
            <div>
              <p className="font-medium">
                {value?.phase === 'finalizing'
                  ? 'Saving and reopening the tested result'
                  : running
                    ? latest
                      ? (remedyLabel[latest.remedy] ?? latest.remedy)
                      : value?.run?.stage || 'Testing the reviewed setup'
                    : 'Checking signals and available price history'}
              </p>
              <p className="mt-1 text-muted-foreground">
                {value?.phase === 'finalizing'
                  ? 'Checking that saved settings and results reopen exactly.'
                  : running
                    ? latest?.reason ||
                      'Research continues in the background. You can leave this page and return later.'
                    : 'Usable stocks will continue; exclusions will be listed in your plan.'}
              </p>
            </div>
            {typeof progress === 'number' && (
              <span className="tabular-nums">{Math.round(progress)}%</span>
            )}
          </div>
          <progress
            aria-label={running ? 'Research progress' : 'Preparation progress'}
            max={100}
            value={progress ?? 0}
            className="h-2 w-full accent-primary"
          />
        </section>
      )}
      {plan && form && !running && value?.phase !== 'none' && value?.phase !== 'complete' && (
        <div className="grid gap-4 md:grid-cols-2">
          <article className="space-y-3 rounded-lg border p-4">
            <h3 className="flex items-center gap-2 text-sm font-medium">
              <Database className="size-4" />
              Available data
            </h3>
            <div className="flex gap-8">
              <div>
                <p className="text-2xl font-semibold tabular-nums">{number(plan.data.stocks)}</p>
                <p className="text-xs text-muted-foreground">stocks</p>
              </div>
              <div>
                <p className="text-2xl font-semibold tabular-nums">{number(plan.data.signals)}</p>
                <p className="text-xs text-muted-foreground">signals</p>
              </div>
            </div>
            <p className="text-sm text-muted-foreground">
              {plan.data.from ?? 'Start date unavailable'} –{' '}
              {plan.data.to ?? 'End date unavailable'}
            </p>
            {plan.data.excluded.length > 0 && (
              <details className="text-sm">
                <summary className="cursor-pointer">
                  {number(plan.data.excluded_count ?? plan.data.excluded.length)} exclusions — view
                  reasons
                </summary>
                {(plan.data.excluded_count ?? 0) > plan.data.excluded.length && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    First {number(plan.data.excluded.length)} shown; complete reasons remain in the
                    saved evidence.
                  </p>
                )}
                <ul className="mt-2 max-h-44 space-y-2 overflow-auto text-xs text-muted-foreground">
                  {plan.data.excluded.map((row, index) => (
                    <li key={`${row.symbol}-${index}`}>
                      <strong className="text-foreground">{row.symbol}</strong> · {row.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </article>
          <article className="space-y-3 rounded-lg border p-4">
            <h3 className="flex items-center gap-2 text-sm font-medium">
              <Settings2 className="size-4" />
              Suggested settings
            </h3>
            <div className="space-y-1.5">
              <Label htmlFor={`journey-${experimentId}-style`} className="text-xs">
                Trading style
              </Label>
              <select
                id={`journey-${experimentId}-style`}
                value={form.style}
                disabled={!canEdit}
                onChange={(event) =>
                  setForm((current) =>
                    current
                      ? { ...current, style: event.target.value as JourneyPlan['style'] }
                      : current
                  )
                }
                className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              >
                <option value="positional">Positional — overnight allowed</option>
                <option value="intraday">Intraday — close within the day</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {field('capital', 'Estimated capital (₹)', 1000)}
              {field('order_size_pct', 'Trade size (%)', 0.1)}
            </div>
            <details className="text-sm">
              <summary className="cursor-pointer text-muted-foreground">
                Holding and exit settings
              </summary>
              <div className="mt-3 grid grid-cols-2 gap-3">
                {form.style === 'positional' &&
                  field('hold_sessions', 'Maximum holding sessions', 1)}
                {field('stop_pct', 'Stop loss (%)', 0.1)}
                {field('target_pct', 'Profit target (%)', 0.1)}
              </div>
            </details>
            <p className="text-xs text-muted-foreground">
              Your trading style and risk limits stay fixed. Research may adjust other trading
              settings and explain why.
            </p>
          </article>
          <article className="space-y-3 rounded-lg border p-4">
            <h3 className="flex items-center gap-2 text-sm font-medium">
              <FlaskConical className="size-4" />
              What will be tested
            </h3>
            <p className="text-sm font-medium">{plan.objective.label}</p>
            <p className="text-xs text-muted-foreground">{plan.objective.reason}</p>
            <ul className="space-y-1.5 text-sm">
              {plan.tests.map((test) => (
                <li key={test} className="flex gap-2">
                  <Check className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  {test}
                </li>
              ))}
            </ul>
            <p className="text-xs text-muted-foreground">
              {plan.engine.label} · {plan.engine.reason}
            </p>
          </article>
          <article className="space-y-3 rounded-lg border p-4">
            <h3 className="flex items-center gap-2 text-sm font-medium">
              <Clock3 className="size-4" />
              Expected time
            </h3>
            <p className="text-sm">{plan.estimate.label}</p>
            <p className="text-xs text-muted-foreground">
              Runs in the background. Saved progress is retained if recovery is needed.
            </p>
            {plan.memory.considered > 0 && (
              <p className="text-xs text-muted-foreground">
                {number(plan.memory.considered)} previous findings considered.{' '}
                {plan.memory.retest_reason}
              </p>
            )}
          </article>
        </div>
      )}
      {value?.phase === 'none' && (
        <div className="space-y-2 rounded-lg bg-muted/40 p-4 text-sm">
          <p>
            Research is saved with its tested results and exclusion reasons. Your previous saved
            setup is retained.
          </p>
          {value.result?.stopping_reason && <p>Stopped because: {value.result.stopping_reason}</p>}
        </div>
      )}
      {value?.phase === 'complete' && value.result && (
        <div className="grid grid-cols-2 gap-4 rounded-lg border p-4">
          <div>
            <p className="text-xs text-muted-foreground">Starting setup P&L</p>
            <p className="mt-1 text-xl font-semibold">{money(value.result.before?.net_pnl)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Selected setup P&L</p>
            <p className="mt-1 text-xl font-semibold">{money(value.result.after?.net_pnl)}</p>
          </div>
          {value.result.verification?.matched &&
            value.result.verification.new_calculations === 0 && (
              <p className="col-span-2 text-xs text-muted-foreground">
                Saved result verified on reopen. No new calculations.
              </p>
            )}
        </div>
      )}
      {value?.decisions?.length ? (
        <details className="text-sm">
          <summary className="cursor-pointer">
            Research decisions ({value.decisions.length})
          </summary>
          <ol className="mt-3 space-y-3 border-l pl-4">
            {value.decisions.map((decision, index) => (
              <li key={`${decision.remedy}-${index}`}>
                <p className="font-medium">
                  {remedyLabel[decision.remedy] ?? decision.remedy}
                  {decision.no_effect?.status === 'unchanged'
                    ? ' · No change; further checks skipped'
                    : decision.outcome
                      ? ` · ${decision.outcome}`
                      : ''}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{decision.reason}</p>
                {decision.expected_evidence && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Expected: {decision.expected_evidence}
                  </p>
                )}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {running
            ? 'You can safely leave this page.'
            : 'Original scanner signals remain unchanged.'}
        </p>
        <div className="flex gap-2">
          {changed && (
            <Button variant="outline" onClick={() => void save()} disabled={!canEdit}>
              {pending === 'save' ? 'Saving…' : 'Save plan changes'}
            </Button>
          )}
          {value?.result?.job_id && (
            <Button variant="outline" onClick={() => void result.current?.(value.result!.job_id)}>
              {value.result.accepted ? 'View saved result' : 'View tested report'}
            </Button>
          )}
          {(!value || !terminal.has(value.phase)) && !running && (
            <Button
              onClick={() => void start()}
              disabled={!value?.can_start || changed || stale || !!pending || !plan}
            >
              <Play className="mr-2 size-4" />
              {pending === 'start' ? 'Starting…' : 'Start research'}
            </Button>
          )}
        </div>
      </div>
    </section>
  )
}
