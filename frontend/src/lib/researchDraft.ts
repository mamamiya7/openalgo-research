import type {
  ConnectorExecution,
  ExperimentKind,
  ResearchConfig,
  ResearchSource,
  SearchSpec,
  Submission,
} from '@/api/scannerResearch'

export const researchDefaults: ResearchConfig = {
  initial_capital: 100000,
  order_size_pct: 10,
  target_pct: 10,
  stop_pct: 5,
  hold_sessions: 5,
  cost_bps: 10,
  slippage_bps: 0,
  trailing_pct: 0,
  trailing_enabled: false,
  modes: ['Bypass'],
  entry_priority: 'csv',
  priority_seed: 0,
  max_exposure_pct: 100,
  exposure_fill_mode: 'strict',
}
export const strategies = [
  ['Bypass'],
  ['Bottom Fishing'],
  ['Zero Only'],
  ['Uptick'],
  ['Bottom Fishing', 'Zero Only'],
  ['Bottom Fishing', 'Uptick'],
  ['Zero Only', 'Uptick'],
  ['Bottom Fishing', 'Zero Only', 'Uptick'],
]
export const searchDefaults: SearchSpec = {
  mode: 'quick',
  axes: {
    target_pct: { min: 5, max: 15, step: 5 },
    stop_pct: { min: 3, max: 7, step: 2 },
    hold_sessions: { min: 3, max: 7, step: 2 },
    trailing_pct: { min: 0, max: 4, step: 2 },
  },
  mode_strategies: [['Bypass']],
  budget: 25,
  rank_by: 'balance',
  include_trailing_off: true,
}
export interface Draft {
  execution?: ConnectorExecution
  config: ResearchConfig
  kind: ExperimentKind
  source: ResearchSource | null
  search: SearchSpec
  research: {
    intent: 'fixed_setup' | 'select_earlier'
    scheme: 'holdout' | 'walk_forward'
    train_end: string
    test_end: string
    gap_sessions: number
    folds: number
    min_train_closed: number
    prior_explored: boolean
  }
  variants: Array<{ name: string; changes: Partial<ResearchConfig> }>
}
export function freshDraft(): Draft {
  return {
    execution: { engine: 'vectorbt', optimizer: 'optuna' },
    config: structuredClone(researchDefaults),
    kind: 'backtest',
    source: null,
    search: structuredClone(searchDefaults),
    research: {
      intent: 'fixed_setup',
      scheme: 'holdout',
      train_end: '',
      test_end: '',
      gap_sessions: 5,
      folds: 1,
      min_train_closed: 10,
      prior_explored: true,
    },
    variants: [],
  }
}
export function readDraft(owner: string): Draft {
  try {
    const stored = sessionStorage.getItem(`research-draft:${owner}`)
    if (stored) {
      const parsed = JSON.parse(stored)
      return {
        ...freshDraft(),
        ...parsed,
        // Older drafts and exact reruns retain their recorded scanner execution.
        execution: parsed.execution,
        config: { ...researchDefaults, ...parsed.config },
      }
    }
  } catch {
    /* Storage may be unavailable. */
  }
  return freshDraft()
}
export function writeDraft(owner: string, draft: Draft) {
  try {
    sessionStorage.setItem(
      `research-draft:${owner}`,
      JSON.stringify({ ...draft, source: draft.source ? compactSourceReceipt(draft.source) : null })
    )
  } catch {
    /* In-memory draft remains usable. */
  }
}
/** Browser recovery keeps identity and qualifications, never the full price lineage. */
function compactSourceReceipt(source: ResearchSource): ResearchSource {
  const select = (record: Record<string, unknown>, keys: string[]) =>
    Object.fromEntries(keys.filter((key) => key in record).map((key) => [key, record[key]]))
  const warnings = (items: unknown): string[] => {
    if (!Array.isArray(items)) return []
    const preview = items.slice(0, 24).map((item) => {
      const text = String(item)
      return text.length > 1000
        ? `${text.slice(0, 1000)}… [Warning shortened in browser receipt; full text retained in saved evidence]`
        : text
    })
    if (items.length > 24)
      preview.push(
        `${items.length - 24} additional warnings retained in saved evidence; reopen the source for full coverage.`
      )
    return preview
  }
  const receipt = source.receipt
  return {
    id: source.id,
    receipt: {
      input_rows: receipt.input_rows,
      signal_count: receipt.signal_count,
      duplicates_removed: receipt.duplicates_removed,
      date_from: receipt.date_from,
      date_to: receipt.date_to,
      symbol_count: receipt.symbol_count,
      warnings: warnings(receipt.warnings),
    },
    provenance: select(source.provenance, [
      'provider',
      'exchange',
      'interval',
      'history_source',
      'temporal_version',
      'timezone',
      'adjustment_basis',
      'calendar_basis',
      'calendar_verified',
      'identity_verified',
      'synthetic',
      'available_through',
      'actions_as_of',
      'warmup_sessions',
      'import_version',
    ]) as ResearchSource['provenance'],
    coverage: {
      ...select(source.coverage, ['status', 'session_count', 'date_from', 'date_to']),
      warnings: warnings(source.coverage.warnings),
      detail_scope:
        'Compact browser receipt only. Per-symbol coverage, instrument identities and raw lineage are not stored in this draft. Reopen the saved source for details; exact immutable evidence remains in saved run exports.',
    },
  }
}
export function specification(draft: Draft): Record<string, unknown> {
  const pins = draft.execution
    ? Object.fromEntries(
        [
          'contract_version',
          'engine_version',
          'adapter_version',
          ...(draft.kind === 'optimize' && draft.execution.optimizer === 'optuna'
            ? ['optimizer_version', 'optimizer_adapter_version']
            : []),
        ]
          .filter((key) => draft.execution?.[key as keyof ConnectorExecution] !== undefined)
          .map((key) => [key, draft.execution?.[key as keyof ConnectorExecution]])
      )
    : {}
  const execution =
    draft.execution &&
    (draft.execution.engine === 'vectorbt' || draft.execution.optimizer === 'optuna')
      ? {
          execution: {
            ...pins,
            engine: draft.execution.engine,
            optimizer: draft.kind === 'optimize' ? draft.execution.optimizer || 'optuna' : 'native',
          },
        }
      : {}
  if (draft.kind === 'backtest') return execution
  if (draft.kind === 'optimize') return { ...draft.search, ...execution }
  if (draft.kind === 'sensitivity') return { variants: draft.variants }
  return {
    ...draft.research,
    variants: draft.variants,
    ...(draft.research.intent === 'select_earlier' ? { search: draft.search } : {}),
  }
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value !== null && typeof value === 'object')
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(',')}}`
  return JSON.stringify(value)
}
type SubmissionReceipt = {
  owner: string
  signature: string
  job_id: string
  status: string
  persisted: boolean
}
let latestSubmission: SubmissionReceipt | null = null
export function submissionHistory(owner: string, payload: Submission): SubmissionReceipt | null {
  const signature = canonical(payload)
  if (
    latestSubmission?.owner === owner &&
    latestSubmission.signature === signature &&
    !latestSubmission.persisted
  )
    return latestSubmission
  try {
    const saved = JSON.parse(sessionStorage.getItem(`research-submission:${owner}`) || 'null')
    return saved?.signature === signature ? saved : null
  } catch {
    return latestSubmission?.owner === owner && latestSubmission.signature === signature
      ? latestSubmission
      : null
  }
}
export function rememberSubmission(
  owner: string,
  payload: Submission,
  job_id: string,
  status: string
) {
  latestSubmission = { owner, signature: canonical(payload), job_id, status, persisted: false }
  try {
    sessionStorage.setItem(`research-submission:${owner}`, JSON.stringify(latestSubmission))
    latestSubmission.persisted = true
  } catch {
    /* Bounded fallback for this submission only. */
  }
}
export function updateSubmissionStatus(
  owner: string,
  payload: Submission,
  job_id: string,
  status: string
) {
  const saved = submissionHistory(owner, payload)
  if (saved?.job_id === job_id) rememberSubmission(owner, payload, job_id, status)
}
/** Same pending payload keeps its token through network retries and reloads. */
let memoryReceipt: { owner: string; signature: string; id: string; persisted: boolean } | null =
  null
export function requestIdentity(owner: string, payload: Submission): Submission {
  return { ...payload, request_id: operationIdentity(owner, 'submit', payload) }
}
/** A linked retry is a distinct attempt; repeats for that attempt keep one token. */
export function operationIdentity(
  owner: string,
  operation: string,
  payload: unknown,
  beginAttempt = false
): string {
  const signature = canonical({ operation, payload }),
    key = `research-request:${owner}`
  if (
    !beginAttempt &&
    memoryReceipt?.owner === owner &&
    memoryReceipt.signature === signature &&
    !memoryReceipt.persisted
  )
    return memoryReceipt.id
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (!beginAttempt && saved?.signature === signature) return saved.id
  } catch {
    if (!beginAttempt && memoryReceipt?.owner === owner && memoryReceipt.signature === signature)
      return memoryReceipt.id
  }
  const id = crypto.randomUUID()
  memoryReceipt = { owner, signature, id, persisted: false }
  try {
    sessionStorage.setItem(key, JSON.stringify({ signature, id }))
    memoryReceipt.persisted = true
  } catch {
    /* One bounded in-memory receipt preserves retries when browser storage is unavailable. */
  }
  return id
}
