import { webClient } from './client'

export interface ResearchMethodDefinition {
  id: string
  label: string
  formula: string
  requirements: string[]
  observations: string
  implementation: string
  availability: string
  missing: string
  equality: string
  comparison_policy: Record<string, unknown>
  support: {
    engine: 'vectorbt'
    asset_profile: 'NSE_long_cash_equities'
    execution_intervals: Array<'D' | '1m'>
    feature_interval: 'D'
  }
  id_hash: string
}

export interface ResearchMethodFamily {
  id: string
  label: string
  method_ids: string[]
  registered_hypothesis_ids: string[]
  formula: string
  domain: Array<Record<string, unknown>>
}

export interface ResearchMethodCoverageRow {
  id: string
  registered: number
  proposed: number
  evaluated: number
  reused: number
  retained_histories: number
  unavailable_histories: number
  untested: number
  budget_deferred: number
  status: 'pending' | 'evaluated' | 'proposed' | 'budget_deferred'
  data_status: 'not_observed' | 'unknown_entries_reported' | 'no_unknown_entries_reported'
  unknown_entry_checks: number
}

export interface ResearchMethodCoverage {
  version: 'research-method-coverage-v1'
  status: 'available' | 'unavailable'
  source: Record<
    | 'inputs_artifact'
    | 'recipe_id'
    | 'recipe_version'
    | 'stock_evidence_id'
    | 'mandate_id'
    | 'checkpoint_id'
    | 'manifest_id'
    | 'search_identity',
    string | null
  >
  registry: {
    version: 'research-method-registry-v1'
    id: string
    recipe_version:
      | 'automatic-hypotheses-v8'
      | 'automatic-hypotheses-v9'
      | 'automatic-hypotheses-v10'
      | 'automatic-hypotheses-v11'
      | 'automatic-hypotheses-v12'
    catalog_id: string
    family_count: number
    hypothesis_count: number
    methods: ResearchMethodDefinition[]
    families: ResearchMethodFamily[]
    blocked_families: Array<{
      id: string
      label: string
      status: 'unsupported'
      reason: string
    }>
  }
  fits: Array<{
    id: 'fit1' | 'fit2' | 'fit3' | 'search'
    status: 'pending' | 'running' | 'complete'
    proposals: number
    proposal_limit: 48
    families: ResearchMethodCoverageRow[]
  }>
  families: ResearchMethodCoverageRow[]
  totals: Record<
    | 'proposals'
    | 'evaluated'
    | 'reused'
    | 'retained_histories'
    | 'unavailable_histories'
    | 'registered_families'
    | 'registered_hypotheses',
    number
  >
  limitations: string[]
  id: string
}

export interface ResearchMethodCoverageView {
  version: 'research-method-coverage-view-v1'
  job_id: string
  available: boolean
  reason: string | null
  checkpoint_artifact: string | null
  result_artifact: string | null
  coverage?: ResearchMethodCoverage | null
}

export const researchMethodCoverage = {
  async get(jobId: string, signal?: AbortSignal): Promise<ResearchMethodCoverageView> {
    return (
      await webClient.get(
        `/scanner-research/api/portfolio/jobs/${encodeURIComponent(jobId)}/method-coverage`,
        { signal, timeout: 15000 }
      )
    ).data
  },
}
