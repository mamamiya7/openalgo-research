import { isAxiosError } from 'axios'
import { webClient } from './client'
import type { PortfolioJob, PortfolioResult } from './portfolioResearch'

export type DecisionState = 'keep' | 'reject' | 'revisit'
export interface ComparisonDecisionTarget {
  comparison_id: string
  member_id: string
}
export interface DirectDecisionTarget {
  job_id: string
  config_id?: string
  expected_report?: { result_artifact: string; analysis_artifact: string | null }
}
export type DecisionTarget = ComparisonDecisionTarget | DirectDecisionTarget
export const decisionTargetKey = (target: DecisionTarget) =>
  'job_id' in target
    ? [
        'report',
        target.job_id,
        target.config_id ?? 'selected',
        target.expected_report?.result_artifact,
        target.expected_report?.analysis_artifact,
      ]
    : ['comparison', target.comparison_id, target.member_id]
export interface LaterEvidenceReceipt {
  id: string
  job_id: string
  report_id: string
  view: 'primary' | 'embedded_later'
  dates: { from: string | null; to: string | null }
  available: boolean
  error?: string
}
export interface EvidenceUseSummary {
  reservation: { from: string; to: string } | null
  reservation_status: 'reserved_in_setup' | 'not_reserved' | 'unknown'
  calculation: 'not_recorded' | 'recorded' | 'unknown'
  opened_at: number | null
  later_opened_at?: number | null
  later_used_for_decision: boolean
  coverage: 'recorded' | 'legacy_unknown'
  overlap: 'recorded' | 'not_found' | 'not_checked'
}
export interface DecisionEvent {
  id: string
  decision_id: string
  revision: number
  supersedes_event_id: string | null
  state: DecisionState
  reason: string
  created_at: number
  candidate_name: string
  comparison_name: string | null
  comparison_id: string | null
  member_id: string | null
  target?: DirectDecisionTarget
  source_job_id: string
  source_result_artifact: string
  config_id: string
  period: 'full' | 'selection'
  trial_number: number | null
  proposal_number: number | null
  is_objective_winner: boolean
  evaluation: LaterEvidenceReceipt | null
  evidence_use: EvidenceUseSummary
}
export interface DecisionSummary {
  id: string
  revision: number
  current: DecisionEvent
  archived: boolean
}
export interface DecisionPage {
  items: DecisionSummary[]
  next_offset: number | null
  total: number
}
export interface DecisionHistory {
  items: DecisionEvent[]
  next_offset: number | null
  total: number
}
export interface DecisionContext {
  target: DecisionTarget
  current: DecisionSummary | null
  evidence_use: EvidenceUseSummary
  evaluation: { items: LaterEvidenceReceipt[]; next_offset: number | null; total: number | null }
  archived: boolean
}
export interface DecisionReport {
  available: boolean
  error?: string
  job: PortfolioJob | null
  result: PortfolioResult | null
}
export interface SavedDecisionEvent {
  event: DecisionEvent
  archived: boolean
  available: boolean
  error?: string
}
export interface DecisionRequest {
  request_id: string
  revision: number
  state: DecisionState
  reason: string
  evaluation_id?: string | null
}
export type OpenedEvidenceTarget =
  | ({ kind: 'comparison_member'; evaluation_id?: string } & ComparisonDecisionTarget)
  | ({ kind: 'direct_report'; evaluation_id?: string } & Pick<
      DirectDecisionTarget,
      'job_id' | 'config_id'
    >)
  | {
      kind: 'decision_event'
      decision_id: string
      event_id: string
      evidence: 'selection' | 'evaluation'
    }
export const decisionOpeningTarget = (
  target: DecisionTarget,
  evaluationId?: string
): OpenedEvidenceTarget =>
  'job_id' in target
    ? {
        kind: 'direct_report',
        job_id: target.job_id,
        ...(target.config_id ? { config_id: target.config_id } : {}),
        ...(evaluationId ? { evaluation_id: evaluationId } : {}),
      }
    : {
        kind: 'comparison_member',
        ...target,
        ...(evaluationId ? { evaluation_id: evaluationId } : {}),
      }
export interface EvidenceOpeningIntent {
  request_id: string
  target: OpenedEvidenceTarget
}
export const decisionKey = (owner: string, experimentId: string) =>
  ['research-decisions', owner, experimentId] as const
export const decisionError = (error: unknown) =>
  isAxiosError(error) && typeof error.response?.data?.message === 'string'
    ? error.response.data.message
    : 'Your decision could not be saved. Try again.'
export const decisionConflict = (error: unknown) =>
  isAxiosError(error) &&
  ['decision_revision_conflict', 'decision_evidence_changed'].includes(error.response?.data?.code)
export const decisionEvidenceChanged = (error: unknown) =>
  isAxiosError(error) && error.response?.data?.code === 'decision_evidence_changed'
const base = (experimentId: string) =>
  `/scanner-research/api/library/experiments/${encodeURIComponent(experimentId)}`
const targetPath = (experimentId: string, target: DecisionTarget) =>
  'job_id' in target
    ? `${base(experimentId)}/results/${encodeURIComponent(target.job_id)}`
    : `${base(experimentId)}/comparisons/${encodeURIComponent(target.comparison_id)}/members/${encodeURIComponent(target.member_id)}`
const targetParams = (target: DecisionTarget) =>
  'job_id' in target && target.config_id ? { config_id: target.config_id } : {}
const path = (experimentId: string, id?: string) =>
  `${base(experimentId)}/decisions${id ? `/${encodeURIComponent(id)}` : ''}`
const eventPath = (experimentId: string, id: string, eventId: string) =>
  `${path(experimentId, id)}/events/${encodeURIComponent(eventId)}`
const options = (signal?: AbortSignal) => ({ signal, timeout: 15000 })
const targetOptions = (target: DecisionTarget, signal?: AbortSignal) => ({
  ...options(signal),
  ...('job_id' in target && target.config_id ? { params: targetParams(target) } : {}),
})
export const researchDecisions = {
  async context(
    experimentId: string,
    target: DecisionTarget,
    offset = 0,
    signal?: AbortSignal
  ): Promise<DecisionContext> {
    return (
      await webClient.get(`${targetPath(experimentId, target)}/decision`, {
        params: {
          ...targetParams(target),
          ...('job_id' in target && target.expected_report
            ? {
                expected_result_artifact: target.expected_report.result_artifact,
                ...(target.expected_report.analysis_artifact
                  ? { expected_analysis_artifact: target.expected_report.analysis_artifact }
                  : {}),
              }
            : {}),
          limit: 20,
          offset,
        },
        ...options(signal),
      })
    ).data
  },
  async save(
    experimentId: string,
    target: DecisionTarget,
    data: DecisionRequest,
    signal?: AbortSignal
  ): Promise<{ decision: DecisionSummary; event: DecisionEvent; reused: boolean }> {
    return (
      await webClient.post(
        `${targetPath(experimentId, target)}/decisions`,
        {
          ...data,
          ...('job_id' in target && target.expected_report
            ? { expected_report: target.expected_report }
            : {}),
        },
        targetOptions(target, signal)
      )
    ).data
  },
  async list(
    experimentId: string,
    state: DecisionState | '',
    offset = 0,
    signal?: AbortSignal
  ): Promise<DecisionPage> {
    return (
      await webClient.get(path(experimentId), {
        params: { ...(state ? { state } : {}), limit: 20, offset },
        ...options(signal),
      })
    ).data
  },
  async history(
    experimentId: string,
    id: string,
    offset = 0,
    signal?: AbortSignal
  ): Promise<DecisionHistory> {
    return (
      await webClient.get(`${path(experimentId, id)}/history`, {
        params: { limit: 20, offset },
        ...options(signal),
      })
    ).data
  },
  async event(
    experimentId: string,
    id: string,
    eventId: string,
    signal?: AbortSignal
  ): Promise<SavedDecisionEvent> {
    return (await webClient.get(eventPath(experimentId, id, eventId), options(signal))).data
  },
  async report(
    experimentId: string,
    id: string,
    eventId: string,
    evidence: 'selection' | 'evaluation',
    signal?: AbortSignal
  ): Promise<DecisionReport> {
    return (
      await webClient.get(`${eventPath(experimentId, id, eventId)}/report`, {
        params: { evidence },
        ...options(signal),
      })
    ).data
  },
  async preview(
    experimentId: string,
    target: DecisionTarget,
    evaluationId: string,
    signal?: AbortSignal
  ): Promise<DecisionReport> {
    return (
      await webClient.get(
        `${targetPath(experimentId, target)}/decision/evaluations/${encodeURIComponent(evaluationId)}`,
        targetOptions(target, signal)
      )
    ).data
  },
  async opened(
    experimentId: string,
    intent: EvidenceOpeningIntent,
    signal?: AbortSignal
  ): Promise<{ opened_at: number; reused: boolean }> {
    return (await webClient.post(`${base(experimentId)}/evidence/opened`, intent, options(signal)))
      .data
  },
}
