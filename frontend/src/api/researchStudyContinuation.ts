import { webClient } from './client'
import type { LibraryJob, ResearchExperiment, SetupVersion } from './researchLibrary'

export interface StudyContinuationContext {
  revision: number
  parent_result_artifact: string
  current_proposed: number
  max_total: number
  available: boolean
  reason: string | null
}
export interface StudyContinuationRequest {
  revision: number
  request_id: string
  parent_result_artifact: string
  additional_trials: number
}
const path = (experiment: string, job: string) =>
  `/scanner-research/api/library/experiments/${encodeURIComponent(experiment)}/studies/${encodeURIComponent(job)}/continue`
export const researchStudyContinuation = {
  async context(
    experiment: string,
    job: string,
    signal?: AbortSignal
  ): Promise<StudyContinuationContext> {
    return (await webClient.get(path(experiment, job), { signal, timeout: 30000 })).data
  },
  async extend(
    experiment: string,
    job: string,
    body: StudyContinuationRequest,
    signal?: AbortSignal
  ): Promise<{
    experiment: ResearchExperiment
    job: LibraryJob
    version: SetupVersion
  }> {
    return (await webClient.post(path(experiment, job), body, { signal, timeout: 30000 })).data
  },
}
