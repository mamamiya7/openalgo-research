import type { ConnectorCatalog } from '@/api/scannerResearch'
import type { Draft } from '@/lib/researchDraft'

/** Early feedback only; the API validates the complete candidate and data scope. */
export function connectorIssue(draft: Draft, catalog?: ConnectorCatalog): string | null {
  const vectorbt = draft.execution?.engine === 'vectorbt'
  const optuna = draft.kind === 'optimize' && (draft.execution?.optimizer === 'optuna' || vectorbt)
  if (
    catalog &&
    vectorbt &&
    !catalog.engines.some((item) => item.id === 'vectorbt' && item.available)
  )
    return 'VectorBT is unavailable. Update the research installation before running a backtest.'
  if (
    catalog &&
    optuna &&
    !catalog.optimizers.some((item) => item.id === 'optuna' && item.available)
  )
    return 'Optuna is unavailable. Update the research installation before optimizing.'
  if (!vectorbt) return null
  const cfg = draft.config
  if (
    draft.source?.provenance.interval === '1m' ||
    cfg.trade_horizon === 'intraday' ||
    cfg.hold_minutes ||
    cfg.entry_time ||
    cfg.exit_time ||
    (draft.kind === 'optimize' &&
      (draft.search.hold_axis === 'hold_minutes' || draft.search.axes.hold_minutes))
  )
    return 'This VectorBT connector supports daily signals. Intraday backtesting is not available yet.'
  if (
    cfg.modes.length !== 1 ||
    cfg.modes[0] !== 'Bypass' ||
    cfg.entry_priority !== 'csv' ||
    cfg.priority_seed !== 0 ||
    cfg.max_exposure_pct !== 100 ||
    cfg.exposure_fill_mode !== 'strict' ||
    (draft.kind === 'optimize' &&
      draft.search.mode_strategies.some((modes) => modes.length !== 1 || modes[0] !== 'Bypass'))
  )
    return 'This saved setup uses filters or allocation rules that VectorBT does not support. Start a new run to use the supported setup.'
  if (cfg.trailing_enabled && cfg.trailing_pct <= 0)
    return 'Enter a positive trailing stop distance or turn the trailing stop off.'
  if (
    draft.kind === 'optimize' &&
    (draft.search.parent_job_id || draft.search.exclude_indices?.length)
  )
    return 'This connector needs a fresh search. Resume the saved run to continue its progress.'
  return null
}

export function ConnectorSettings({
  draft,
  catalog,
}: {
  draft: Draft
  catalog?: ConnectorCatalog
}) {
  const vectorbt = draft.execution?.engine === 'vectorbt'
  const optuna = draft.kind === 'optimize' && (draft.execution?.optimizer === 'optuna' || vectorbt)
  const issue = connectorIssue(draft, catalog)
  return (
    <div className="space-y-2 text-sm">
      <p className="text-muted-foreground">
        {vectorbt
          ? optuna
            ? 'VectorBT · Optuna optimization'
            : 'VectorBT · Daily backtest'
          : `Legacy scanner setup${optuna ? ' · Optuna' : ''}. New run uses VectorBT.`}
      </p>
      {issue && <p role="alert">{issue}</p>}
    </div>
  )
}
