import type { PortfolioDraft } from '@/components/research/PortfolioBuilder'
import { webClient } from './client'
import type { PortfolioJob, PortfolioStrategy } from './portfolioResearch'
import type { DecisionState } from './researchDecisions'
import type { ResearchExperiment } from './researchLibrary'

export interface ChosenSetup {
  id: string
  sequence: number
  name: string
  version: { id: string; number: number; name: string }
  decision_id: string
  event_id: string
  source_job_id: string
  source_result_artifact: string
  config_id: string
  period: 'full' | 'selection'
  created_at: number
  current_decision_state: DecisionState
  usable: boolean
  unavailable_reason?: string | null
  strategies: PortfolioStrategy[]
  capital: number
  currency?: string | null
  dates: { from: string | null; to: string | null }
}
export interface ChosenSetupContext {
  revision: number
  archived: boolean
  current: ChosenSetup | null
  eligibility?: {
    available: boolean
    reason?: string | null
    candidate_name: string
    decision_id: string
    event_id: string
    strategies: PortfolioStrategy[]
    capital: number | null
    currency?: string | null
    period: 'full' | 'selection'
    dates: { from: string | null; to: string | null }
  }
}
export interface ReuseSetupSpec {
  choice_id: string
  sources?: Record<string, string>
  mode?: 'backtest' | 'optimize'
}
export type ReuseSetupRequest = ReuseSetupSpec & { revision: number; request_id: string }
export interface ReuseSourceSummary {
  source_id: string
  filename: string | null
  signal_count: number
  symbol_count: number
  date_from: string | null
  date_to: string | null
}
export interface ReuseSetupPreview {
  revision: number
  archived: boolean
  choice: ChosenSetup
  draft: PortfolioDraft
  changes: {
    sources: Array<{
      strategy_id: string
      name: string
      before: ReuseSourceSummary
      after: ReuseSourceSummary
    }>
    fields: Array<{
      key: 'date_from' | 'date_to' | 'mode' | 'search'
      before: string | null
      after: string | null
    }>
    rules_unchanged: boolean
  }
}
export const chosenSetupKey = (owner: string, experimentId: string) =>
  ['research-chosen-setups', owner, experimentId] as const
const path = (id: string) =>
  `/scanner-research/api/library/experiments/${encodeURIComponent(id)}/chosen-setup`
const options = (signal?: AbortSignal) => ({ signal, timeout: 15000 })
export const researchChosenSetups = {
  async preview(
    id: string,
    data: ReuseSetupSpec,
    signal?: AbortSignal
  ): Promise<ReuseSetupPreview> {
    return (await webClient.post(`${path(id)}/preview`, data, options(signal))).data
  },
  async context(
    id: string,
    decision?: { decision_id: string; event_id: string },
    signal?: AbortSignal
  ): Promise<ChosenSetupContext> {
    return (
      await webClient.get(path(id), {
        ...options(signal),
        ...(decision ? { params: decision } : {}),
      })
    ).data
  },
  async choose(
    id: string,
    data: {
      revision: number
      request_id: string
      decision_id: string
      event_id: string
      name: string
    },
    signal?: AbortSignal
  ): Promise<{ choice: ChosenSetup; revision: number; reused: boolean }> {
    return (await webClient.post(path(id), data, options(signal))).data
  },
  async use(
    id: string,
    data: ReuseSetupRequest,
    signal?: AbortSignal
  ): Promise<{
    choice: ChosenSetup
    revision: number
    reused: boolean
    experiment: ResearchExperiment
  }> {
    return (await webClient.post(`${path(id)}/use`, data, options(signal))).data
  },
  async replay(
    id: string,
    data: { choice_id: string; revision: number; request_id: string },
    signal?: AbortSignal
  ): Promise<{ experiment: ResearchExperiment; job: PortfolioJob; reused: boolean }> {
    return (await webClient.post(`${path(id)}/replay`, data, options(signal))).data
  },
  async history(
    id: string,
    offset = 0,
    signal?: AbortSignal
  ): Promise<{ items: ChosenSetup[]; next_offset: number | null; total: number }> {
    return (
      await webClient.get(`${path(id)}/history`, {
        ...options(signal),
        params: { limit: 20, offset },
      })
    ).data
  },
}
