import { webClient } from './client'

export type StudyProposalState =
  | 'running'
  | 'evaluated'
  | 'reused'
  | 'allocation_rejected'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface StudyExecution {
  id: string
  started_at: number
  finished_at: number | null
  observed_at: number
  state:
    | 'running'
    | 'completed'
    | 'failed'
    | 'cancelled'
    | 'paused'
    | 'interrupted'
    | 'observation_failed'
  reason_code: string | null
  proposal_budget: number
  replayed: number
}

export interface StudyActivityRow {
  id: number
  execution_id: string
  /** Actual stored proposal number; add one only for display. */
  number: number
  config_id: string
  params: Record<string, number>
  state: StudyProposalState
  value: number | null
  reused: boolean
  started_at: number
  finished_at: number | null
  observed_at: number
  reason_code: string | null
  checkpointed: boolean
}

export interface StudyActivityReceipt {
  version: 'research-study-activity-v1'
  job_id: string
  job_status: string
  available: boolean
  reason: 'not_recorded' | null
  executions: StudyExecution[]
  executions_truncated: boolean
  counts: Record<'recorded' | StudyProposalState, number>
  rows: StudyActivityRow[]
  next_before: number | null
}

export const researchStudyActivity = {
  async get(
    jobId: string,
    options: { before?: number; execution?: string } = {},
    signal?: AbortSignal
  ): Promise<StudyActivityReceipt> {
    return (
      await webClient.get(
        `/scanner-research/api/portfolio/jobs/${encodeURIComponent(jobId)}/activity`,
        { params: { limit: 25, ...options }, signal, timeout: 15000 }
      )
    ).data
  },
}
