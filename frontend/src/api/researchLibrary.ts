import type { PortfolioDraft } from '@/components/research/PortfolioBuilder'
import { webClient } from './client'
import type { PortfolioJob, PortfolioRequest } from './portfolioResearch'
import type { ChosenSetup } from './researchChosenSetups'

export interface ExperimentSummary {
  id: string
  name: string
  notes: string
  tags: string[]
  pinned: boolean
  archived: boolean
  revision: number
  created_at: number
  updated_at: number
  parent_job_id?: string | null
  job_count: number
  active_job_count?: number
  version_count: number
  chosen_setup?: ChosenSetup | null
}
export interface SetupVersionSummary {
  id: string
  experiment_id: string
  experiment_name?: string
  name: string
  number: number
  created_at: number
  parent_job_id?: string | null
  parent_trial_id?: string | null
  parent_result_artifact?: string | null
}
export interface SetupVersion extends SetupVersionSummary {
  draft: PortfolioDraft
  portfolio: PortfolioRequest | null
}
export interface LibraryJob extends PortfolioJob {
  experiment_id: string
  experiment_name?: string
  version_id?: string
  role?: string
}
export interface ResearchExperiment extends ExperimentSummary {
  draft: PortfolioDraft
  jobs: LibraryJob[]
  versions: SetupVersion[]
  jobs_next_offset: number | null
  versions_next_offset: number | null
}
export type LibraryPage<T> = { items: T[]; next_offset: number | null }
export type LibraryFilter = {
  search?: string
  archived?: boolean
  limit?: number
  offset?: number
}
const base = '/scanner-research/api/library'
const path = (id: string) => `${base}/experiments/${encodeURIComponent(id)}`
const options = { timeout: 30000 }
export const researchLibrary = {
  async replay(
    id: string,
    revision: number,
    job_id: string,
    request_id: string,
    trial_id?: string,
    period?: 'selection' | 'evaluation'
  ): Promise<{ experiment: ResearchExperiment; job: LibraryJob; version: SetupVersion }> {
    return (
      await webClient.post(
        `${path(id)}/replay`,
        {
          revision,
          job_id,
          request_id,
          ...(trial_id ? { trial_id } : {}),
          ...(period ? { period } : {}),
        },
        options
      )
    ).data
  },
  async list(
    params: LibraryFilter = {},
    signal?: AbortSignal
  ): Promise<LibraryPage<ExperimentSummary>> {
    return (await webClient.get(`${base}/experiments`, { params, signal, ...options })).data
  },
  async studies(
    params: LibraryFilter = {},
    signal?: AbortSignal
  ): Promise<LibraryPage<LibraryJob>> {
    return (await webClient.get(`${base}/studies`, { params, signal, ...options })).data
  },
  async versions(
    params: LibraryFilter = {},
    signal?: AbortSignal
  ): Promise<LibraryPage<SetupVersionSummary>> {
    return (await webClient.get(`${base}/versions`, { params, signal, ...options })).data
  },
  async get(
    id: string,
    signal?: AbortSignal,
    params?: { jobs_offset?: number; versions_offset?: number }
  ): Promise<ResearchExperiment> {
    return (await webClient.get(path(id), { signal, params, ...options })).data
  },
  async create(data: {
    name?: string
    notes?: string
    tags?: string[]
    draft?: PortfolioDraft
  }): Promise<ResearchExperiment> {
    return (await webClient.post(`${base}/experiments`, data, options)).data
  },
  async update(
    id: string,
    data: { revision: number } & Partial<
      Pick<ExperimentSummary, 'name' | 'notes' | 'tags' | 'pinned' | 'archived'>
    >
  ): Promise<ResearchExperiment> {
    return (await webClient.patch(path(id), data, options)).data
  },
  async saveDraft(
    id: string,
    revision: number,
    draft: PortfolioDraft
  ): Promise<ResearchExperiment> {
    return (await webClient.put(`${path(id)}/draft`, { revision, draft }, options)).data
  },
  async saveVersion(
    id: string,
    revision: number,
    name?: string
  ): Promise<{ experiment: ResearchExperiment; version: SetupVersion }> {
    return (
      await webClient.post(`${path(id)}/versions`, { revision, ...(name ? { name } : {}) }, options)
    ).data
  },
  async restoreVersion(id: string, version: string, revision: number): Promise<ResearchExperiment> {
    return (
      await webClient.post(
        `${path(id)}/versions/${encodeURIComponent(version)}/restore`,
        { revision },
        options
      )
    ).data
  },
  async getVersion(id: string, version: string, signal?: AbortSignal): Promise<SetupVersion> {
    return (
      await webClient.get(`${path(id)}/versions/${encodeURIComponent(version)}`, {
        signal,
        ...options,
      })
    ).data
  },
  async run(
    id: string,
    revision: number,
    request_id: string
  ): Promise<{ experiment: ResearchExperiment; version: SetupVersion; job: LibraryJob }> {
    return (await webClient.post(`${path(id)}/run`, { revision, request_id }, options)).data
  },
  async fromJob(data: {
    job_id: string
    trial_id?: string
    mode: 'backtest' | 'optimize'
    name?: string
    request_id: string
    experiment_id?: string
    revision?: number
  }): Promise<ResearchExperiment> {
    return (await webClient.post(`${base}/experiments/from-job`, data, options)).data
  },
  async remove(id: string, revision: number): Promise<{ deleted: boolean; id: string }> {
    return (await webClient.delete(path(id), { data: { revision }, ...options })).data
  },
}
