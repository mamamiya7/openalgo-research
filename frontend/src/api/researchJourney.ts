import { webClient } from './client'
import type { PreparationStatus, ResearchExperiment } from './researchLibrary'
import type { ResearchActivity } from './scannerResearch'

export interface JourneySettings {
  capital: number
  order_size_pct: number
  hold_sessions: number
  stop_pct: number
  target_pct: number
  trailing_pct?: number
  allocation_pct?: number
}
export interface JourneyFlow {
  version: 'research-user-flow-v1'
  intent: 'replay' | 'improve'
  involvement: 'hands_off' | 'guided' | 'hands_on'
  adjustable: Array<'capital' | 'order_size_pct' | 'hold_sessions' | 'stop_pct' | 'target_pct' | 'trailing_pct' | 'allocation_pct'>
}
export interface JourneyStrategySettings {
  id?: string
  name?: string
  allocation_pct: number
  config: Record<string, unknown>
  execution_policy?: Record<string, unknown>
}
export interface JourneyPlan {
  id: string
  revision: number
  style: 'intraday' | 'positional'
  objective: { id: string; label: string; reason: string }
  engine: { id: string; label: string; reason: string }
  data: {
    stocks: number
    signals: number
    from: string | null
    to: string | null
    eligible_signals?: number
    excluded_count?: number
    excluded: Array<{ symbol: string; reason: string }>
  }
  settings: JourneySettings
  baseline_settings?: JourneyStrategySettings[]
  tests: string[]
  estimate: { seconds: number | null; label: string }
  memory: { considered: number; retest_reason?: string }
  budget?: { proposals: number; max_simulations: number }
  contract?: { signal_policy?: string; planning_policy?: string }
  flow?: JourneyFlow
}
export interface JourneyStopReview {
  option_ids: string[]
  reviews: Array<{ option: number; evidence_ref: string; reason: string }>
  complete: boolean
  unreviewed_options: number[]
}
export interface JourneyStopping {
  kind: string
  decision_source: string
  reason: string
  model_budget_exhausted: boolean
  model_calls_spent: number
  model_call_limit: number
  native_accounts_remaining: number
  option_count: number
  unreviewed_option_ids: string[]
  stop_review?: JourneyStopReview | null
}
export interface JourneyDecision {
  remedy: string
  reason: string
  problem?: string
  expected_evidence?: string
  outcome?: string | null
  no_effect?: { status: string; kind?: string } | null
  diagnosis?: {
    target_ref: string
    cause: string
    prediction: string
    falsifier: string
    version?: 'research-failure-diagnosis-v1'
    context_key?: string
    candidate_id?: string
    origin_job_id?: string
  } | null
  selection?: string
  reasons?: string[]
  stop_review?: JourneyStopReview | null
  stop_review_rejected?: {
    evidence_id: string
    source_result_artifact: string
    reason?: string
  } | null
  planning?: {
    version: 'hands-off-plan-v1'
    action: 'search' | 'defer' | 'stop'
    method: 'holding_and_exits' | 'sizing_and_allocation' | 'joint_trading' | null
    hypothesis: string
    reason: string
    expected_evidence: string
    stop_rule: string
    domains: Record<string, Record<string, { min: number; max: number; step: number }>>
  }
  change?: {
    axis: string
    from?: number | string | boolean | null
    to?: number | string | boolean | null
    current_capital?: number
  }
  settings_change?: {
    status: 'proposed' | 'tested' | 'verified' | 'blocked' | 'unavailable'
    rows: Array<{
      strategy_id?: string | null
      strategy: string
      field: string
      before: number | string | boolean | null
      after: number | string | boolean | null
    }>
  }
}
export interface JourneyState {
  version: string
  phase:
    | 'preparing'
    | 'review'
    | 'queued'
    | 'researching'
    | 'finalizing'
    | 'awaiting_approval'
    | 'complete'
    | 'none'
    | 'needs_action'
  revision: number
  preparation: PreparationStatus
  plan: JourneyPlan | null
  flow?: JourneyFlow
  pending_approval?: {
    id: string
    kind: 'search' | 'defer' | 'stop' | 'continuation'
    reason: string
    hypothesis?: string
    domains?: NonNullable<JourneyDecision['planning']>['domains']
    settings?: JourneyStrategySettings[]
    job_id: string
  } | null
  run: {
    job_id: string
    status: string
    progress: number
    stage?: string
    activity?: ResearchActivity | null
  } | null
  decisions: JourneyDecision[]
  notification_channels?: Array<'slack' | 'discord'>
  notifications?: Array<{
    channel: 'slack' | 'discord'
    status: 'watching' | 'sending' | 'sent' | 'not_sent' | 'uncertain' | 'cancelled'
    detail?: string
  }>
  result: {
    job_id: string
    experiment_id: string
    accepted: boolean
    kind?: 'replay' | 'research' | 'stopped'
    headline: string
    stopping_reason?: string
    stopping?: JourneyStopping | null
    before?: Record<string, unknown>
    after?: Record<string, unknown>
    choice_id?: string
    verification?: { matched?: boolean; new_calculations?: number }
  } | null
  can_start: boolean
  error?: string | null
  experiment?: ResearchExperiment
}
export type JourneyPlanUpdate = JourneySettings & {
  revision: number
  style: JourneyPlan['style']
  flow?: JourneyFlow
}
const path = (id: string) =>
  `/scanner-research/api/library/experiments/${encodeURIComponent(id)}/journey`
const options = { timeout: 30000 }
export const researchJourney = {
  async get(id: string, signal?: AbortSignal): Promise<JourneyState> {
    return (await webClient.get(path(id), { ...options, signal })).data
  },
  async prepare(id: string, revision: number, signal?: AbortSignal): Promise<JourneyState> {
    return (await webClient.post(`${path(id)}/prepare`, { revision }, { ...options, signal })).data
  },
  async updatePlan(
    id: string,
    data: JourneyPlanUpdate,
    signal?: AbortSignal
  ): Promise<JourneyState> {
    return (await webClient.put(`${path(id)}/plan`, data, { ...options, signal })).data
  },
  async start(
    id: string,
    data: {
      revision: number
      plan_id: string
      request_id: string
      notification_channels?: Array<'slack' | 'discord'>
    },
    signal?: AbortSignal
  ): Promise<JourneyState> {
    return (await webClient.post(`${path(id)}/start`, data, { ...options, signal })).data
  },
  async approve(
    id: string,
    data: { revision: number; approval_id: string; decision: 'approve' | 'reject' },
    signal?: AbortSignal
  ): Promise<JourneyState> {
    return (await webClient.post(`${path(id)}/approve`, data, { ...options, signal })).data
  },
}
