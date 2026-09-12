import { isAxiosError } from 'axios'
import { webClient } from './client'
import type { AnalysisMetric, PortfolioResult, ScalarAnalysis } from './portfolioResearch'
import type { CandidateReportReceipt } from './researchCandidates'

export interface ShortlistCandidate {
  id: string
  experiment_id: string
  source_job_id: string
  source_result_artifact: string
  config_id: string
  period: 'full' | 'selection'
  origin_kind: 'study' | 'backtest'
  /** Stored zero-based configuration's first actual trial and inspected proposal. */
  trial_number: number | null
  proposal_number: number | null
  is_objective_winner: boolean
  name: string
  note: string
  revision: number
  created_at: number
  updated_at: number
  snapshot: {
    summary: Record<string, number | string | boolean | null>
    dates: { from: string | null; to: string | null }
    capital: number | null
    currency: string | null
    engine: string | null
    objective: { key: string; score: number } | null
  }
  archived: boolean
  source_available: boolean
  source_error?: string
  report: Pick<CandidateReportReceipt, 'status' | 'report_job_id' | 'error'>
}
export interface ShortlistPage {
  version: 'research-shortlist-v1'
  experiment_id: string
  archived: boolean
  items: ShortlistCandidate[]
  next_offset: number | null
  total: number
}
export interface ShortlistDetail {
  candidate: ShortlistCandidate
  archived: boolean
  available: boolean
  error?: string
  strategies: PortfolioResult['strategies'] | null
  analysis: ScalarAnalysis | null
  analysis_catalog: AnalysisMetric[] | null
  report: ShortlistCandidate['report']
}
export interface SaveCandidate {
  job_id: string
  config_id?: string
  proposal_number?: number
  expected_result_artifact?: string
}
const path = (experimentId: string, id?: string) =>
  `/scanner-research/api/library/experiments/${encodeURIComponent(experimentId)}/shortlist${id ? `/${encodeURIComponent(id)}` : ''}`
const options = (signal?: AbortSignal) => ({ signal, timeout: 15000 })
export const shortlistKey = (owner: string, experimentId: string) =>
  ['research-shortlist', owner, experimentId] as const
export const shortlistError = (error: unknown) =>
  isAxiosError(error) && typeof error.response?.data?.message === 'string'
    ? error.response.data.message
    : 'Your shortlist could not be updated. Try again.'
export const isShortlistConflict = (error: unknown) =>
  isAxiosError(error) && error.response?.data?.code === 'shortlist_revision_conflict'

export const researchShortlist = {
  async list(
    experimentId: string,
    params: { offset?: number; job_id?: string; config_id?: string } = {},
    signal?: AbortSignal
  ): Promise<ShortlistPage> {
    return (
      await webClient.get(path(experimentId), {
        params: { limit: 20, ...params },
        ...options(signal),
      })
    ).data
  },
  async get(experimentId: string, id: string, signal?: AbortSignal): Promise<ShortlistDetail> {
    return (await webClient.get(path(experimentId, id), options(signal))).data
  },
  async save(
    experimentId: string,
    body: SaveCandidate,
    signal?: AbortSignal
  ): Promise<{ candidate: ShortlistCandidate; reused: boolean }> {
    return (await webClient.post(path(experimentId), body, options(signal))).data
  },
  async update(
    experimentId: string,
    id: string,
    body: { revision: number; name?: string; note?: string },
    signal?: AbortSignal
  ): Promise<ShortlistCandidate> {
    return (await webClient.patch(path(experimentId, id), body, options(signal))).data
  },
  async remove(
    experimentId: string,
    id: string,
    revision: number,
    signal?: AbortSignal
  ): Promise<{ removed: true; id: string }> {
    return (
      await webClient.delete(path(experimentId, id), { data: { revision }, ...options(signal) })
    ).data
  },
}
