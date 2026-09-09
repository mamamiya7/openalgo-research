import { webClient } from './client'

export interface ResearchConfig {
  initial_capital: number
  order_size_pct: number
  target_pct: number
  stop_pct: number
  hold_sessions: number
  cost_bps: number
  slippage_bps: number
  trailing_pct: number
  trailing_enabled: boolean
  modes: string[]
  entry_priority: 'csv' | 'alphabetical' | 'reversed' | 'shuffle'
  priority_seed: number
  max_exposure_pct: number
  exposure_fill_mode: 'strict' | 'remaining'
  trade_horizon?: 'intraday' | 'multiday'
  hold_minutes?: number | null
  entry_time?: string | null
  exit_time?: string | null
}
export type ExperimentKind = 'backtest' | 'optimize' | 'research' | 'sensitivity'
export interface ConnectorExecution {
  engine: 'auto' | 'scanner' | 'vectorbt'
  optimizer?: 'native' | 'optuna'
  engine_version?: string
  adapter_version?: string
  contract_version?: string
  optimizer_version?: string
  optimizer_adapter_version?: string
}
export interface ConnectorCatalog {
  engines: Array<{ id: string; name: string; available: boolean; intervals?: string[] }>
  optimizers: Array<{ id: string; name: string; available: boolean; engines?: string[] }>
}
export interface SearchSpec {
  hold_axis?: 'hold_minutes' | 'hold_sessions'
  trailing_choices?: Array<{ enabled: boolean; pct: number }>
  experiment_version?: string
  parent_job_id?: string
  mode: 'quick' | 'full' | 'exhaustive' | 'auto'
  axes: Record<string, { min: number; max: number; step: number }>
  mode_strategies: string[][]
  budget: number
  rank_by: 'balance' | 'return' | 'drawdown'
  include_trailing_off: boolean
  exclude_indices?: number[]
}
export interface Submission {
  source_id: string
  config: ResearchConfig
  kind: ExperimentKind
  specification: Record<string, unknown>
  request_id?: string
}
export interface Candidate {
  config_id: string
  config: ResearchConfig
  summary: Record<string, unknown>
  score: number
  grid_index: number
  stage: string
}
export interface VariantResult {
  name: string
  changes: Partial<ResearchConfig>
  report: ResearchResult
  return_change_pp: number
}
export interface Experiment {
  specification?: Record<string, unknown>
  pass_rows?: Candidate[]
  exploration?: { prior_explored: boolean; known_overlap_jobs: string[]; interpretation: string }
  kind: ExperimentKind
  state?: string
  findings: string[]
  next_action?: string
  counts?: Record<string, unknown>
  recommendation_id?: string
  alternatives?: string[]
  rows?: Candidate[]
  selected_reports?: Record<string, ResearchResult>
  neighborhoods?: Array<Record<string, unknown>>
  follow_up?: Record<string, unknown> | null
  variants?: VariantResult[]
  folds?: Array<{
    window: Record<string, unknown>
    training_ranks: Candidate[]
    selected_config: ResearchConfig | null
    test_report: ResearchResult | null
    sensitivities: VariantResult[]
    finding: string
  }>
}
export interface ResearchSource {
  id: string
  receipt: {
    filename?: string
    input_rows: number
    signal_count: number
    duplicates_removed: number
    date_from: string
    date_to: string
    symbol_count: number
    warnings: string[]
  }
  coverage: Record<string, unknown>
  provenance: {
    provider: string
    exchange: string
    interval: string
    adjustment_basis: string
    calendar_basis: string
    synthetic: boolean
  }
}
export interface ResearchResult {
  execution?: ConnectorExecution
  config: ResearchConfig
  policy_version: string
  metric_basis: string
  summary: Record<string, unknown>
  equity_curve: Array<Record<string, unknown>>
  ledger: Array<Record<string, unknown>>
  coverage: Record<string, unknown>
  limits: string[]
  experiment?: Experiment
  prepared_source?: ResearchSource
}
export interface ResearchJob {
  id: string
  source_id: string
  status: string
  progress: number
  created_at: number | string
  config: ResearchConfig
  error?: string | null
  result?: ResearchResult
  kind?: ExperimentKind | 'prepare_source' | 'acquire' | 'evidence_update'
  title?: string
  source_summary?: Record<string, unknown>
  previous_attempt_id?: string | null
  specification?: Record<string, unknown>
  counts?: Record<string, unknown>
  queue_position?: number | null
  resumable?: boolean
}
export interface JobPage {
  items: ResearchJob[]
  next_cursor: string | null
}
export interface WorkerHealth {
  worker_state: 'online' | 'offline' | 'stale' | 'maintenance'
  worker_online: boolean
  maintenance: boolean
  last_heartbeat: number | null
  active_jobs: number
}
export interface SourceCapabilities {
  connectors?: ConnectorCatalog
  public: {
    available: boolean
    date_from?: string
    date_to?: string
    calendar_version?: string
    calendar_years?: number[]
    import_version?: string
    extension_available?: boolean
    extension_supported?: boolean
    range_basis?: string
    message?: string
  }
  evidence_update_available: boolean
  history?: {
    configured: boolean
    stored_available: boolean
    can_prepare: boolean
  }
  broker: {
    provider: string
    configured: boolean
    connected?: boolean
    state?: string
    action_url?: string
    message?: string
  }
}
const base = '/scanner-research/api'
export const scannerResearch = {
  async upload(
    file: File,
    source: string,
    requirements?: Omit<Submission, 'source_id' | 'request_id'>
  ): Promise<ResearchSource | { preparation_job: ResearchJob }> {
    const form = new FormData()
    form.append('file', file)
    form.append('source', source)
    if (requirements) form.append('requirements', JSON.stringify(requirements))
    return (await webClient.post(`${base}/sources`, form)).data
  },
  async sources(): Promise<ResearchSource[]> {
    return (await webClient.get(`${base}/sources`)).data
  },
  async source(id: string): Promise<ResearchSource> {
    return (await webClient.get(`${base}/sources/${encodeURIComponent(id)}`)).data
  },
  async jobs(
    params: { page_size?: number; cursor?: string; q?: string; kind?: string; status?: string } = {}
  ): Promise<JobPage> {
    return (await webClient.get(`${base}/jobs`, { params })).data
  },
  async health(): Promise<WorkerHealth> {
    return (await webClient.get(`${base}/health`)).data
  },
  async capabilities(): Promise<SourceCapabilities> {
    return (await webClient.get(`${base}/source-capabilities`)).data
  },
  async retry(id: string, request_id: string): Promise<ResearchJob> {
    return (await webClient.post(`${base}/jobs/${encodeURIComponent(id)}/retry`, { request_id }))
      .data
  },
  async updateEvidence(
    source_id: string,
    end_date: string,
    request_id: string
  ): Promise<ResearchJob> {
    return (await webClient.post(`${base}/evidence-updates`, { source_id, end_date, request_id }))
      .data
  },
  async job(id: string): Promise<ResearchJob> {
    return (await webClient.get(`${base}/jobs/${encodeURIComponent(id)}`)).data
  },
  async submit(payload: Submission): Promise<ResearchJob> {
    return (await webClient.post(`${base}/jobs`, payload)).data
  },
  async preflight(payload: Submission): Promise<Record<string, unknown>> {
    return (await webClient.post(`${base}/preflight`, payload)).data
  },
  async resume(id: string): Promise<ResearchJob> {
    return (await webClient.post(`${base}/jobs/${encodeURIComponent(id)}/resume`)).data
  },
  async cancel(id: string): Promise<ResearchJob> {
    return (await webClient.post(`${base}/jobs/${encodeURIComponent(id)}/cancel`)).data
  },
  exportUrl(id: string) {
    return `${import.meta.env.VITE_API_URL || ''}${base}/jobs/${encodeURIComponent(id)}/export`
  },
}
