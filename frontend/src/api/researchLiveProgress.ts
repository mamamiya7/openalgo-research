import { webClient } from './client'

export interface LiveResearchMetrics {
  score: number | null
  return_pct: number | null
  drawdown_pct: number | null
  closed_trades: number | null
}
export interface LiveResearchTrial extends LiveResearchMetrics {
  /** Recorded zero-based proposal number within this fit. */
  trial_number: number | null
}
export interface LiveResearchProgressReceipt {
  version: 'research-live-progress-v1'
  job_id: string
  job_status: string
  available: boolean
  provisional: true
  checkpoint_artifact: string | null
  reason?: string | null
  stage: string
  fit: {
    key: string
    label: string
    dates?: { from: string; to: string }
    completed: number
    budget: number
  } | null
  latest: LiveResearchTrial | null
  best: LiveResearchTrial | null
  baseline: LiveResearchMetrics | null
  events: Array<{ kind: string; text: string }>
  pace: {
    seconds_per_trial: number | null
    remaining_search_seconds_upper: number | null
  }
}

export const researchLiveProgress = {
  async get(jobId: string, signal?: AbortSignal): Promise<LiveResearchProgressReceipt> {
    return (
      await webClient.get(
        `/scanner-research/api/portfolio/jobs/${encodeURIComponent(jobId)}/live-progress`,
        { signal, timeout: 15000 }
      )
    ).data
  },
}
