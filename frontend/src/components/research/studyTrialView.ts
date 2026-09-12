import type { PortfolioResult, PortfolioTrial } from '@/api/portfolioResearch'
import { availableTrialMetrics, trialMetricValue } from './portfolioTrialMetrics'

export interface StudyTrialView {
  scope: 'distinct' | 'all'
  sortKey: string
  sortDirection: 'asc' | 'desc'
  page: number
  selectedConfigId?: string | null
}

export const defaultStudyTrialView: StudyTrialView = {
  scope: 'distinct',
  sortKey: 'objective_score',
  sortDirection: 'desc',
  page: 0,
  selectedConfigId: null,
}

type Experiment = NonNullable<PortfolioResult['experiment']>
export interface StudyTrialRow {
  key: string
  number: number
  configId: string
  candidate: PortfolioTrial | null
  status: 'complete' | 'reused' | 'rejected'
  score: number | null
}

const finite = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

/** The all-proposal view is recorded Optuna evidence, never synthesized from counts. */
export function studyTrialRows(
  experiment: Experiment,
  scope: StudyTrialView['scope']
): StudyTrialRow[] {
  const candidates = new Map<string, PortfolioTrial>()
  for (const row of experiment.rows) {
    if (!candidates.has(row.config_id)) candidates.set(row.config_id, row)
  }
  if (scope === 'all') {
    return (experiment.trials ?? []).map((proposal) => ({
      key: `proposal:${proposal.number}`,
      number: proposal.number,
      configId: proposal.config_id,
      candidate:
        proposal.state === 'complete' ? (candidates.get(proposal.config_id) ?? null) : null,
      status: proposal.state === 'pruned' ? 'rejected' : proposal.reused ? 'reused' : 'complete',
      score: proposal.state === 'complete' ? finite(proposal.value) : null,
    }))
  }
  return [...candidates.values()].map((candidate) => ({
    key: `config:${candidate.config_id}`,
    number: candidate.trial_number,
    configId: candidate.config_id,
    candidate,
    status: 'complete',
    score: finite(candidate.score),
  }))
}

export function sortStudyTrials(
  rows: StudyTrialRow[],
  experiment: Experiment,
  view: Pick<StudyTrialView, 'sortKey' | 'sortDirection'>
): StudyTrialRow[] {
  const metric = availableTrialMetrics(experiment.rows, experiment.analysis_catalog).find(
    (item) => item.key === view.sortKey && item.format !== 'text'
  )
  const value = (row: StudyTrialRow) => {
    if (view.sortKey === 'trial_number') return row.number
    if (view.sortKey === 'objective_score') return row.score
    if (!metric || !row.candidate) return null
    return finite(trialMetricValue(metric, row.candidate))
  }
  return [...rows].sort((left, right) => {
    const a = value(left)
    const b = value(right)
    if (a === null && b !== null) return 1
    if (a !== null && b === null) return -1
    if (a !== null && b !== null && a !== b) {
      return (a - b) * (view.sortDirection === 'asc' ? 1 : -1)
    }
    return left.number - right.number || left.configId.localeCompare(right.configId)
  })
}

export function studyTrialPage(rows: StudyTrialRow[], requestedPage: number, pageSize = 25) {
  const page = Math.max(
    0,
    Math.min(
      Number.isInteger(requestedPage) ? requestedPage : 0,
      Math.ceil(rows.length / pageSize) - 1
    )
  )
  return { page, rows: rows.slice(page * pageSize, (page + 1) * pageSize) }
}
