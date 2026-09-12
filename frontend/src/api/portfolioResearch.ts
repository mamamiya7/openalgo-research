import { webClient } from './client'
import type { ResearchActivity, ResearchConfig, ResearchSource } from './scannerResearch'

export type PortfolioAxis =
  | 'target_pct'
  | 'stop_pct'
  | 'hold_sessions'
  | 'hold_minutes'
  | 'trailing_pct'
  | 'order_size_pct'
  | 'allocation_pct'
export type PortfolioRange = { min: number; max: number; step: number }
export interface PortfolioCapabilities {
  max_strategies: number
  engines: Array<{
    id: 'vectorbt' | 'nautilus'
    name: string
    available: boolean
    reason?: string
    intervals: string[]
  }>
  optimizers: Array<{ id: 'optuna'; available: boolean; samplers: Array<'tpe' | 'grid'> }>
}
export interface PortfolioStrategy {
  id: string
  name: string
  type: 'signals'
  source_id: string
  allocation_pct: number
  config: ResearchConfig
  search: Partial<Record<PortfolioAxis, PortfolioRange>>
}
export type PortfolioSettings = Pick<PortfolioStrategy, 'id' | 'name' | 'allocation_pct' | 'config'>
export interface AnalysisMetric {
  key: string
  label: string
  group: string
  format: 'percent' | 'money' | 'number' | 'text'
  description: string
  source: string
}
export interface AnalysisChart {
  id: string
  title: string
  status: 'available' | 'unavailable'
  reason?: string
  figure?: { data: Record<string, unknown>[]; layout: Record<string, unknown> }
}
export interface ScalarAnalysis {
  version: string
  metrics: Record<string, number | string | null>
  unavailable: Record<string, string>
}
export interface StudyAnalysis {
  version: string
  basis: string[]
  charts: AnalysisChart[]
  parameters?: string[]
}
export interface PortfolioAnalysis extends ScalarAnalysis, StudyAnalysis {
  catalog: AnalysisMetric[]
  price_symbols?: string[]
  price_symbol?: string
  report_depth?: {
    version: 'research-report-depth-v1'
    daily_return_quantiles: {
      status: 'available' | 'unavailable'
      reason?: string
      rows: Array<{ percentile: number; return_pct: number }>
      [key: string]: unknown
    }
    drawdowns: {
      status: 'available' | 'unavailable'
      reason?: string
      total: number
      shown: number
      truncated: boolean
      basis: string
      duration_definition: string
      recovery_definition: string
      rows: Array<{
        id: number
        peak_at: string | null
        start_at: string
        trough_at: string
        end_at: string
        recovered_at: string | null
        status: 'recovered' | 'ongoing'
        depth_pct: number
        peak_equity: number
        trough_equity: number
        underwater_bars: number
        underwater_sessions: number
        recovery_sessions: number | null
        recovery_days: number | null
        peak_index: number | null
        trough_index: number
        end_index: number
      }>
    }
    rolling: {
      windows: number[]
      annual_sessions: number
      sampling: string
      volatility_ddof: number
      risk_free_return: number
      required_return: number
      sortino_definition: string
    }
  }
}
export interface PortfolioPeriodPlan {
  version: 'research-period-plan-v1'
  mode: 'reserve' | 'evaluate'
  train_pct: number
  selection: { from: string; to: string }
  evaluation: { from: string; to: string }
  signal_dates_sha256: string
  basis: string
  positions: string
}
export type EvaluationBasis =
  | { version: 'research-evaluation-basis-v1'; status: 'unverified' }
  | {
      version: 'research-evaluation-basis-v1'
      status: 'verified'
      evidence_id: string
      source_id: string
      cohort_id: string
      observations_id: string
      prices_id: string
      calendar_id: string
      instruments_id: string
      period: {
        kind: 'selection' | 'evaluation' | 'full'
        from: string
        to: string
        observations: number
        interval: string
      }
      admission: { eligible: number; excluded: number; pending: number }
      comparison: {
        id: string
        period: 'selection' | 'evaluation' | 'full'
        currency: string
        capital: number
        execution: Record<string, unknown>
        costs: Array<{ strategy_id: string; cost_bps: number; slippage_bps: number }>
      }
    }
export interface PortfolioReportContext {
  version: 'research-report-context-v1'
  report_id: string
  job_id: string
  result_artifact: string
  inputs_artifact: string | null
  config_id: string | null
  period: 'selection' | 'evaluation' | 'full'
  period_label: string
  dates: { from: string | null; to: string | null }
  analysis_version: string | null
  analysis_artifact: string | null
  evaluation_basis?: EvaluationBasis
  parent_job_id?: string
  candidate?: {
    study_job_id: string
    trial_number: number
    config_id: string
    is_objective_winner: boolean
  }
}
export interface PortfolioRequest {
  version: 'research-portfolio-v1'
  name: string
  capital: number
  engine: 'vectorbt' | 'nautilus'
  strategies: PortfolioStrategy[]
  optimization?: {
    sampler: 'tpe' | 'grid'
    trials: number
    objective: 'balanced' | 'return' | 'drawdown'
    seed: number
  }
  date_from?: string
  date_to?: string
  validation?: { train_pct: number; mode?: 'reserve' | 'evaluate' }
}
export interface PortfolioTrial {
  trial_number: number
  config_id: string
  strategies: PortfolioSettings[]
  summary: Record<string, number | string | null>
  score: number
  stage: string
  analysis?: ScalarAnalysis
}
export interface PortfolioProposal {
  number: number
  params: Record<string, number>
  config_id: string
  state: 'complete' | 'pruned'
  value: number | null
  reused: boolean
  datetime_start?: string
  datetime_complete?: string
}
export interface PortfolioResult {
  study_continuation?: {
    version: string
    parent_job_id: string
    parent_result_artifact: string
    replayed_proposals: number
    retained_portfolios: number
    total_proposals: number
    additional_proposals: number
  }
  matched_baseline_origin?: {
    version: string
    study_job_id: string
    study_result_artifact: string
    baseline_job_id: string
    baseline_result_artifact: string
    verification?: { settings: string; basis: string }
  }
  report_context?: PortfolioReportContext
  evaluation_basis?: EvaluationBasis
  reserved_evaluation?: {
    version: 'research-period-plan-v1'
    selection: { from: string; to: string }
    evaluation: { from: string; to: string }
    status: 'reserved'
  }
  analysis?: PortfolioAnalysis
  engine_records?: Record<string, Array<Record<string, unknown>>>
  portfolio?: PortfolioRequest
  config: { initial_capital: number }
  strategies: PortfolioSettings[]
  summary: Record<string, number | string | null>
  equity_curve: Array<{
    date: string
    timestamp?: string
    equity: number
    cash: number
    drawdown_pct: number
  }>
  ledger: Array<Record<string, string | number | null>>
  per_strategy: Array<{
    id: string
    name: string
    allocation_pct: number
    net_pnl: number
    contribution_pct: number
    summary: Record<string, number | string | null>
  }>
  source?: {
    provider: string
    interval: string
    signal_count: number
    strategy_count: number
    eligible_signals?: number
    excluded_signals?: number
  }
  validation?: {
    train_from: string
    train_to: string
    test_from: string
    test_to: string
    training_signals: number
    testing_signals: number
    result: PortfolioResult
    label: string
    selection_basis: string
  }
  execution?: { engine: string; engine_version?: string; interval?: string }
  experiment?: {
    kind: 'portfolio_optimize'
    evaluation_basis_id?: string
    rows: PortfolioTrial[]
    trials?: PortfolioProposal[]
    recommendation_id: string
    selected_strategies: PortfolioSettings[]
    optimizer: { sampler: string; objective_definition: string; version: string }
    specification: NonNullable<PortfolioRequest['optimization']>
    counts: Record<string, number>
    analysis_catalog?: AnalysisMetric[]
    study_analysis?: StudyAnalysis
    search_space?: { axes: Record<string, PortfolioRange>; proposal_budget?: number }
  }
}
export interface ResearchResultDescriptor {
  version: 'research-result-descriptor-v1'
  role:
    | 'baseline'
    | 'matched_baseline'
    | 'optimization'
    | 'candidate'
    | 'evaluation'
    | 'replay'
    | 'backtest'
  evidence_id: string | null
  report_id: string | null
  config_id: string | null
  calculation_id: string | null
  period: 'selection' | 'evaluation' | 'full' | null
  dates: { from: string | null; to: string | null; status: 'recorded' | 'unknown' }
  input_dates: { from: string | null; to: string | null }
  candidate: {
    study_job_id: string
    config_id: string
    trial_number?: number
    is_objective_winner?: boolean
    period?: string
  } | null
  parent_job_id: string | null
  account: { capital: number | null; currency: string | null }
  interval: string | null
  evaluation_basis_id: string | null
  cohort_id: string | null
  reservation: Record<string, unknown> | null
  setup: { id: string; name: string; number: number; parent_version_id?: string } | null
}
export interface PortfolioJob {
  display?: ResearchResultDescriptor
  updated_at?: number | string
  activity?: ResearchActivity
  id: string
  status: string
  progress: number
  created_at: number | string
  title?: string
  kind?: string
  error?: string | null
  resumable?: boolean
  pausable?: boolean
  queue_position?: number | null
  counts?: Record<string, unknown>
  specification?: { portfolio?: PortfolioRequest }
  source_summary?: { signal_count?: number; date_from?: string; date_to?: string; name?: string }
  result?: PortfolioResult
}
export interface PortfolioPreview {
  period_plan?: PortfolioPeriodPlan
  portfolio: PortfolioRequest
  interval: string
  receipt: ResearchSource['receipt'] & { strategy_count: number }
  versions: Record<string, string>
}
export interface PortfolioAnalysisStatus {
  status: 'queued' | 'running' | 'complete' | 'failed' | 'missing'
  error?: string
  job?: PortfolioJob
  analysis_job_id?: string
}
export type PortfolioSource = ResearchSource & {
  created_at?: number
  receipt: ResearchSource['receipt'] & { name?: string; filename?: string; input_type?: string }
}
const base = '/scanner-research/api'
export const portfolioResearch = {
  async sources(
    offset = 0,
    signal?: AbortSignal
  ): Promise<{ items: PortfolioSource[]; next_offset: number | null }> {
    return (
      await webClient.get(`${base}/sources`, {
        params: { portfolio_inputs: 1, limit: 20, offset },
        signal,
        timeout: 15000,
      })
    ).data
  },
  async capabilities(signal?: AbortSignal): Promise<PortfolioCapabilities> {
    return (await webClient.get(`${base}/portfolio/capabilities`, { signal, timeout: 15000 })).data
  },
  async upload(file: File, signal?: AbortSignal): Promise<ResearchSource> {
    const form = new FormData()
    form.append('file', file)
    return (
      await webClient.post(`${base}/portfolio/inputs`, form, {
        timeout: 45000,
        ...(signal ? { signal } : {}),
      })
    ).data
  },
  async preflight(portfolio: PortfolioRequest, signal?: AbortSignal): Promise<PortfolioPreview> {
    return (
      await webClient.post(`${base}/portfolio/preflight`, portfolio, { signal, timeout: 30000 })
    ).data
  },
  async submit(portfolio: PortfolioRequest, request_id: string): Promise<PortfolioJob> {
    return (
      await webClient.post(`${base}/portfolio/jobs`, { portfolio, request_id }, { timeout: 30000 })
    ).data
  },
  async job(id: string, signal?: AbortSignal): Promise<PortfolioJob> {
    return (
      await webClient.get(`${base}/jobs/${encodeURIComponent(id)}`, { signal, timeout: 15000 })
    ).data
  },
  async jobs(
    params: { page_size?: number; cursor?: string; kind?: string } = {},
    signal?: AbortSignal
  ): Promise<{ items: PortfolioJob[]; next_cursor: string | null }> {
    return (await webClient.get(`${base}/jobs`, { params, signal, timeout: 15000 })).data
  },
  async pause(id: string, signal?: AbortSignal): Promise<PortfolioJob> {
    return (
      await webClient.post(`${base}/jobs/${encodeURIComponent(id)}/pause`, undefined, {
        timeout: 15000,
        signal,
      })
    ).data
  },
  async cancel(id: string, signal?: AbortSignal): Promise<PortfolioJob> {
    return (
      await webClient.post(`${base}/jobs/${encodeURIComponent(id)}/cancel`, undefined, {
        timeout: 15000,
        signal,
      })
    ).data
  },
  async resume(id: string, signal?: AbortSignal): Promise<PortfolioJob> {
    return (
      await webClient.post(`${base}/jobs/${encodeURIComponent(id)}/resume`, undefined, {
        timeout: 30000,
        signal,
      })
    ).data
  },
  async rerun(
    id: string,
    request_id: string,
    trial_id?: string,
    period?: 'selection' | 'evaluation'
  ): Promise<PortfolioJob> {
    return (
      await webClient.post(
        `${base}/portfolio/jobs/${encodeURIComponent(id)}/rerun`,
        {
          request_id,
          ...(trial_id ? { trial_id } : {}),
          ...(period ? { period } : {}),
        },
        { timeout: 30000 }
      )
    ).data
  },
  async prepareAnalysis(
    id: string,
    parameters?: string[],
    symbol?: string,
    period?: 'selection' | 'validation'
  ): Promise<PortfolioAnalysisStatus> {
    return (
      await webClient.post(
        `${base}/portfolio/jobs/${encodeURIComponent(id)}/analysis`,
        {
          ...(parameters ? { parameters } : {}),
          ...(symbol ? { symbol } : {}),
          ...(period ? { period } : {}),
        },
        { timeout: 30000 }
      )
    ).data
  },
  async analysis(id: string, signal?: AbortSignal): Promise<PortfolioAnalysisStatus> {
    return (
      await webClient.get(`${base}/portfolio/jobs/${encodeURIComponent(id)}/analysis`, {
        signal,
        timeout: 15000,
      })
    ).data
  },
  analysisExportUrl(id: string) {
    return `${import.meta.env.VITE_API_URL || ''}${base}/portfolio/jobs/${encodeURIComponent(id)}/analysis/export`
  },
  exportUrl(id: string) {
    return `${import.meta.env.VITE_API_URL || ''}${base}/jobs/${encodeURIComponent(id)}/export`
  },
}
