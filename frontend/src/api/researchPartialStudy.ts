import { webClient } from './client'
import type { AnalysisMetric, PortfolioTrial } from './portfolioResearch'

export interface PartialStudyReceipt {
  version: 'research-partial-study-v1'
  job_id: string
  status: string
  available: boolean
  reason: string | null
  checkpoint_artifact: string | null
  checkpoint_id: string | null
  scope: 'search' | 'selection' | 'full' | null
  automatic: boolean
  counts: { proposed: number; evaluated: number; reused: number; rejected: number }
  total_rows: number
  offset: number
  limit: number
  next_offset: number | null
  rows: Array<Omit<PortfolioTrial, 'stage'>>
  analysis_catalog: AnalysisMetric[]
}

export const researchPartialStudy = {
  async get(
    jobId: string,
    options: { offset?: number; expected_checkpoint?: string } = {},
    signal?: AbortSignal
  ): Promise<PartialStudyReceipt> {
    return (
      await webClient.get(
        `/scanner-research/api/portfolio/jobs/${encodeURIComponent(jobId)}/partial-study`,
        { params: { limit: 25, ...options }, signal, timeout: 15000 }
      )
    ).data
  },
}
