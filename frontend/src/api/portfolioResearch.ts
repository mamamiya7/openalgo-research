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
  validation?: { train_pct: 80 }
}
export interface PortfolioTrial {
  trial_number: number
  config_id: string
  strategies: PortfolioSettings[]
  summary: Record<string, number | string | null>
  score: number
  stage: string
}
export interface PortfolioResult {
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
    rows: PortfolioTrial[]
    recommendation_id: string
    selected_strategies: PortfolioSettings[]
    optimizer: { sampler: string; objective_definition: string; version: string }
    specification: NonNullable<PortfolioRequest['optimization']>
    counts: Record<string, number>
  }
}
export interface PortfolioJob {
  activity?: ResearchActivity
  id: string
  status: string
  progress: number
  created_at: number | string
  title?: string
  kind?: string
  error?: string | null
  resumable?: boolean
  queue_position?: number | null
  counts?: Record<string, unknown>
  specification?: { portfolio?: PortfolioRequest }
  source_summary?: { signal_count?: number; date_from?: string; date_to?: string; name?: string }
  result?: PortfolioResult
}
export interface PortfolioPreview {
  portfolio: PortfolioRequest
  interval: string
  receipt: ResearchSource['receipt'] & { strategy_count: number }
  versions: Record<string, string>
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
  async upload(file: File): Promise<ResearchSource> {
    const form = new FormData()
    form.append('file', file)
    return (await webClient.post(`${base}/portfolio/inputs`, form, { timeout: 45000 })).data
  },
  async preflight(portfolio: PortfolioRequest): Promise<PortfolioPreview> {
    return (await webClient.post(`${base}/portfolio/preflight`, portfolio, { timeout: 30000 })).data
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
  async cancel(id: string): Promise<PortfolioJob> {
    return (
      await webClient.post(`${base}/jobs/${encodeURIComponent(id)}/cancel`, undefined, {
        timeout: 15000,
      })
    ).data
  },
  async resume(id: string): Promise<PortfolioJob> {
    return (
      await webClient.post(`${base}/jobs/${encodeURIComponent(id)}/resume`, undefined, {
        timeout: 30000,
      })
    ).data
  },
  async rerun(id: string, request_id: string, trial_id?: string): Promise<PortfolioJob> {
    return (
      await webClient.post(
        `${base}/portfolio/jobs/${encodeURIComponent(id)}/rerun`,
        {
          request_id,
          ...(trial_id ? { trial_id } : {}),
        },
        { timeout: 30000 }
      )
    ).data
  },
  exportUrl(id: string) {
    return `${import.meta.env.VITE_API_URL || ''}${base}/jobs/${encodeURIComponent(id)}/export`
  },
}
