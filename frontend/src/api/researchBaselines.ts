import { webClient } from './client'
import type { PortfolioJob } from './portfolioResearch'

export interface BaselineMatch {
  version: string
  revision: number
  archived: boolean
  study: { job_id: string; result_artifact: string }
  baseline: { job_id: string; result_artifact: string }
  period: 'selection' | 'full' | null
  dates: { from: string | null; to: string | null }
  recipe: {
    config_id: string
    evaluation_basis_id: string
    eligible_signals: number
    excluded_signals: number
  } | null
  differences: string[]
  action: {
    kind: 'prepare' | 'open' | 'progress' | 'unavailable'
    job_id?: string
    reason?: string
  }
}
const path = (experiment: string, study: string, baseline: string) =>
  `/scanner-research/api/library/experiments/${encodeURIComponent(experiment)}/studies/${encodeURIComponent(study)}/baselines/${encodeURIComponent(baseline)}/match`
export const baselineMatchKey = (owner: string, experiment: string) =>
  ['research-baseline-match', owner, experiment] as const
const options = (signal?: AbortSignal) => ({ signal, timeout: 15000 })
export const researchBaselines = {
  async context(
    experiment: string,
    study: string,
    baseline: string,
    signal?: AbortSignal
  ): Promise<BaselineMatch> {
    return (await webClient.get(path(experiment, study, baseline), options(signal))).data
  },
  async prepare(
    experiment: string,
    study: string,
    baseline: string,
    data: {
      revision: number
      request_id: string
      study_result_artifact: string
      baseline_result_artifact: string
    },
    signal?: AbortSignal
  ): Promise<{ job: PortfolioJob; reused: boolean; context: BaselineMatch }> {
    return (await webClient.post(path(experiment, study, baseline), data, options(signal))).data
  },
}
