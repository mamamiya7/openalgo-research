import { webClient } from './client'
import type { PortfolioJob } from './portfolioResearch'
import type { DecisionSummary, DirectDecisionTarget, EvidenceUseSummary } from './researchDecisions'

export interface ValidationContext {
  version: string
  experiment_id: string
  revision: number
  archived: boolean
  candidate: {
    source_job_id: string
    source_result_artifact: string
    config_id: string
    trial_number: number | null
    is_objective_winner: boolean
    report_job_id?: string | null
  }
  selection: { from: string | null; to: string | null }
  reservation: { from: string; to: string } | null
  evidence_use: EvidenceUseSummary
  current?: DecisionSummary | null
  evaluations: Array<{
    job_id: string
    status: PortfolioJob['status']
    progress: number
    resumable?: boolean
    evidence_id?: string
    identity?: { result_artifact: string; analysis_artifact: string | null; config_id: string }
    dates?: { from: string; to: string }
    view?: 'primary' | 'embedded_later'
  }>
  action: {
    kind: 'prepare' | 'open' | 'progress' | 'unavailable'
    job_id?: string
    evidence_id?: string
    view?: 'primary' | 'embedded_later'
    reason?: string
  }
}
export const validationKey = (owner: string, experimentId: string) =>
  ['research-validation', owner, experimentId] as const
const path = (experimentId: string, target: DirectDecisionTarget) =>
  `/scanner-research/api/library/experiments/${encodeURIComponent(experimentId)}/results/${encodeURIComponent(target.job_id)}/validation`
const options = (target: DirectDecisionTarget, signal?: AbortSignal) => ({
  signal,
  timeout: 15000,
  params: target.config_id ? { config_id: target.config_id } : {},
})
export const researchValidation = {
  async context(
    experimentId: string,
    target: DirectDecisionTarget,
    signal?: AbortSignal
  ): Promise<ValidationContext> {
    return (await webClient.get(path(experimentId, target), options(target, signal))).data
  },
  async prepare(
    experimentId: string,
    target: DirectDecisionTarget,
    data: {
      revision: number
      request_id: string
      source_result_artifact: string
    },
    signal?: AbortSignal
  ): Promise<{ job: PortfolioJob; reused: boolean; context: ValidationContext }> {
    return (await webClient.post(path(experimentId, target), data, options(target, signal))).data
  },
}
