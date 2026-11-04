import { webClient } from './client'
import type { PreparationStatus, ResearchExperiment } from './researchLibrary'
import type { ResearchActivity } from './scannerResearch'

export interface JourneySettings {
  capital: number
  order_size_pct: number
  hold_sessions: number
  stop_pct: number
  target_pct: number
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
  tests: string[]
  estimate: { seconds: number | null; label: string }
  memory: { considered: number; retest_reason?: string }
}
export interface JourneyDecision {
  remedy: string
  reason: string
  problem?: string
  expected_evidence?: string
  outcome?: string | null
  no_effect?: { status: string } | null
}
export interface JourneyState {
  version: string
  phase:
    | 'preparing'
    | 'review'
    | 'queued'
    | 'researching'
    | 'finalizing'
    | 'complete'
    | 'none'
    | 'needs_action'
  revision: number
  preparation: PreparationStatus
  plan: JourneyPlan | null
  run: {
    job_id: string
    status: string
    progress: number
    stage?: string
    activity?: ResearchActivity | null
  } | null
  decisions: JourneyDecision[]
  result: {
    job_id: string
    experiment_id: string
    accepted: boolean
    headline: string
    stopping_reason?: string
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
    data: { revision: number; plan_id: string; request_id: string },
    signal?: AbortSignal
  ): Promise<JourneyState> {
    return (await webClient.post(`${path(id)}/start`, data, { ...options, signal })).data
  },
}
