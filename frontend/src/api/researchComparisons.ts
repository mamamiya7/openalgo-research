import { isAxiosError } from 'axios'
import { webClient } from './client'
import type {
  AnalysisChart,
  PortfolioJob,
  PortfolioReportContext,
  PortfolioResult,
} from './portfolioResearch'

export interface ComparisonMetric {
  key: string
  label: string
  format: 'percent' | 'money' | 'number' | 'text'
  source: 'summary' | 'analysis'
  description: string
  values: Record<string, number | string | null>
  deltas: Record<string, number | null> | null
  unavailable: Record<string, string>
}
export interface ComparisonMember {
  id: string
  name: string
  note: string
  origin_kind: 'study' | 'backtest'
  source_job_id: string
  source_result_artifact: string
  config_id: string
  period: 'full' | 'selection'
  trial_number: number | null
  proposal_number: number | null
  is_objective_winner: boolean
  report_job_id: string
  report_result_artifact: string
  analysis_job_id: string | null
  analysis_artifact: string | null
  report_context: PortfolioReportContext
  summary: Record<string, number | string | null>
  available: boolean
  error?: string
}
export interface ComparisonSummary {
  id: string
  experiment_id: string
  number: number
  name: string
  note: string
  revision: number
  created_at: number
  updated_at: number
  archived: boolean
  member_count: number
  member_names: string[]
  reference_member_id: string
  compatible: boolean
  currency: string | null
}
export interface SavedComparison extends ComparisonSummary {
  version: 'research-comparison-v1'
  members: ComparisonMember[]
  differences: Array<{ member_id: string; codes: string[] }>
  metrics: ComparisonMetric[]
  cumulative: AnalysisChart
}
export interface ComparisonPage {
  items: ComparisonSummary[]
  next_offset: number | null
  total: number
}
export interface ComparisonReport {
  member: ComparisonMember
  available: boolean
  error?: string
  job: PortfolioJob | null
  result: PortfolioResult | null
}
export interface ComparisonRequest {
  request_id: string
  candidate_ids: string[]
  reference_candidate_id: string
}
export const comparisonKey = (owner: string, experimentId: string) =>
  ['research-comparisons', owner, experimentId] as const
export const comparisonError = (error: unknown) =>
  isAxiosError(error) && typeof error.response?.data?.message === 'string'
    ? error.response.data.message
    : 'The comparison could not be saved. Try again.'
export const comparisonConflict = (error: unknown) =>
  isAxiosError(error) && error.response?.data?.code === 'comparison_revision_conflict'
const path = (experimentId: string, id?: string) =>
  `/scanner-research/api/library/experiments/${encodeURIComponent(experimentId)}/comparisons${id ? `/${encodeURIComponent(id)}` : ''}`
const options = (signal?: AbortSignal) => ({ signal, timeout: 15000 })
export const researchComparisons = {
  async list(experimentId: string, offset = 0, signal?: AbortSignal): Promise<ComparisonPage> {
    return (
      await webClient.get(path(experimentId), { params: { limit: 20, offset }, ...options(signal) })
    ).data
  },
  async get(experimentId: string, id: string, signal?: AbortSignal): Promise<SavedComparison> {
    return (await webClient.get(path(experimentId, id), options(signal))).data
  },
  async member(
    experimentId: string,
    id: string,
    memberId: string,
    signal?: AbortSignal
  ): Promise<ComparisonReport> {
    return (
      await webClient.get(
        `${path(experimentId, id)}/members/${encodeURIComponent(memberId)}`,
        options(signal)
      )
    ).data
  },
  async save(
    experimentId: string,
    body: ComparisonRequest,
    signal?: AbortSignal
  ): Promise<{ comparison: SavedComparison; reused: boolean }> {
    return (await webClient.post(path(experimentId), body, options(signal))).data
  },
  async update(
    experimentId: string,
    id: string,
    body: { revision: number; name?: string; note?: string },
    signal?: AbortSignal
  ): Promise<SavedComparison> {
    return (await webClient.patch(path(experimentId, id), body, options(signal))).data
  },
}
