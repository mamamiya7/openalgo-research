import { webClient } from './client'

export interface CandidateReportReceipt {
  config_id: string
  /** Stored zero-based trial number. Add one only for display. */
  trial_number: number
  is_objective_winner: boolean
  status: 'ready' | 'available' | 'queued' | 'running' | 'failed' | 'unavailable' | 'mismatch'
  report_job_id?: string
  error?: string
}

export interface CandidateReportsReceipt {
  version: 'research-candidate-reports-v1'
  study_job_id: string
  period: 'selection' | 'full'
  candidates: CandidateReportReceipt[]
}

const path = (studyJobId: string) =>
  `/scanner-research/api/portfolio/jobs/${encodeURIComponent(studyJobId)}/candidates`

export const researchCandidates = {
  async get(studyJobId: string, signal?: AbortSignal): Promise<CandidateReportsReceipt> {
    return (await webClient.get(path(studyJobId), { signal, timeout: 15000 })).data
  },
  async prepare(
    studyJobId: string,
    configId: string,
    signal?: AbortSignal
  ): Promise<CandidateReportReceipt> {
    return (
      await webClient.post(
        `${path(studyJobId)}/${encodeURIComponent(configId)}/report`,
        {},
        { signal, timeout: 30000 }
      )
    ).data
  },
}
