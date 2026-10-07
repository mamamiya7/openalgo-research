import { describe, expect, it } from 'vitest'
import type { PortfolioResult, PortfolioTrial } from '@/api/portfolioResearch'
import {
  defaultStudyTrialView,
  sortStudyTrials,
  studyTrialPage,
  studyTrialRows,
} from './studyTrialView'

const candidate = (number: number, value: number | null): PortfolioTrial => ({
  trial_number: number,
  config_id: `config-${number}`,
  score: 12 - number,
  stage: 'tpe',
  strategies: [],
  summary: { net_return_pct: value, closed_trades: 0 },
})
const makeStudy = (rows: PortfolioTrial[]): NonNullable<PortfolioResult['experiment']> => ({
  kind: 'portfolio_optimize',
  rows,
  recommendation_id: rows[0]?.config_id ?? '',
  selected_strategies: [],
  counts: {},
  optimizer: { sampler: 'TPE', version: '1', objective_definition: 'saved score' },
  specification: { sampler: 'tpe', trials: 100, objective: 'balanced', seed: 0 },
})

describe('saved study trial views', () => {
  it('keeps 100 recorded proposals separate from 65 actual portfolios and 35 reused evaluations', () => {
    const study = makeStudy(Array.from({ length: 65 }, (_, index) => candidate(index, index)))
    study.trials = Array.from({ length: 100 }, (_, number) => ({
      number,
      config_id: `config-${number % 65}`,
      params: { target: number % 65 },
      state: 'complete' as const,
      reused: number >= 65,
      value: 12 - (number % 65),
    }))
    const distinct = studyTrialRows(study, 'distinct')
    const all = studyTrialRows(study, 'all')
    expect(distinct).toHaveLength(65)
    expect(all).toHaveLength(100)
    expect(all.filter((row) => row.status === 'reused')).toHaveLength(35)
    expect(new Set(all.map((row) => row.key)).size).toBe(100)
    expect(all.every((row) => row.candidate?.config_id === row.configId)).toBe(true)
    expect(all[99]).toMatchObject({ number: 99, configId: 'config-34', status: 'reused' })
    const ordered = sortStudyTrials(all, study, defaultStudyTrialView)
    expect(studyTrialPage(ordered, 3).rows).toHaveLength(25)
    expect(ordered[0]).toMatchObject({ number: 0, configId: 'config-0' })
  })

  it('sorts numeric values, preserves zero and orders undefined/nonfinite last in both directions', () => {
    const study = makeStudy([
      candidate(8, null),
      candidate(5, 2),
      candidate(9, Number.POSITIVE_INFINITY),
      candidate(3, -2),
      candidate(6, 10),
      candidate(4, 0),
      candidate(7, null),
    ])
    delete study.rows[6].summary.net_return_pct
    const original = JSON.stringify(study)
    const rows = studyTrialRows(study, 'distinct')
    const sortKey = 'net_return_pct'
    expect(
      sortStudyTrials(rows, study, { sortKey, sortDirection: 'asc' }).map((r) => r.number)
    ).toEqual([3, 4, 5, 6, 7, 8, 9])
    expect(
      sortStudyTrials(rows, study, { sortKey, sortDirection: 'desc' }).map((r) => r.number)
    ).toEqual([6, 5, 4, 3, 7, 8, 9])
    expect(JSON.stringify(study)).toBe(original)
  })

  it('keeps stable original proposal identities for ties, not indexes in score order', () => {
    const study = makeStudy([candidate(67, 10), candidate(13, 10), candidate(0, 10)])
    const rows = studyTrialRows(study, 'distinct')
    for (const sortDirection of ['asc', 'desc'] as const) {
      expect(
        sortStudyTrials(rows, study, { sortKey: 'net_return_pct', sortDirection }).map((row) => [
          row.number,
          row.configId,
        ])
      ).toEqual([
        [0, 'config-0'],
        [13, 'config-13'],
        [67, 'config-67'],
      ])
    }
  })

  it('links actual repeated proposals to their saved configuration and gives rejections no report or score', () => {
    const study = makeStudy([candidate(4, 20), candidate(11, 6)])
    study.trials = [
      {
        number: 4,
        config_id: 'config-4',
        params: { target: 4 },
        value: 8,
        state: 'complete',
        reused: false,
      },
      {
        number: 7,
        config_id: 'invalid-allocation',
        params: { allocation: 120 },
        value: null,
        state: 'pruned',
        reused: false,
      },
      {
        number: 11,
        config_id: 'config-11',
        params: { target: 11 },
        value: 1,
        state: 'complete',
        reused: false,
      },
      {
        number: 14,
        config_id: 'config-4',
        params: { target: 4 },
        value: 8,
        state: 'complete',
        reused: true,
      },
    ]
    const rows = studyTrialRows(study, 'all')
    expect(rows.map((row) => row.number)).toEqual([4, 7, 11, 14])
    expect(rows[1]).toMatchObject({ candidate: null, score: null, status: 'rejected' })
    expect(rows[3].candidate).toBe(study.rows[0])
    expect(rows[3]).toMatchObject({ number: 14, configId: 'config-4', score: 8, status: 'reused' })
    expect(sortStudyTrials(rows, study, defaultStudyTrialView).map((row) => row.number)).toEqual([
      4, 14, 11, 7,
    ])
    expect(studyTrialRows(study, 'distinct')).toHaveLength(2)
  })

  it('does not synthesize missing proposal history or candidate evidence from counters', () => {
    const study = makeStudy([candidate(5, 10)])
    study.counts = { reused_trials: 50, rejected_allocations: 10, evaluated_this_pass: 1 }
    expect(studyTrialRows(study, 'all')).toEqual([])
    study.trials = [
      { number: 70, config_id: 'unknown', params: {}, value: 1, reused: false, state: 'complete' },
    ]
    expect(studyTrialRows(study, 'all')[0]).toMatchObject({ number: 70, candidate: null, score: 1 })
  })

  it('reads native per-candidate numbers without parsing rounded labels or copying winner metrics', () => {
    const study = makeStudy([candidate(2, 1), candidate(1, 2), candidate(0, 3)])
    study.analysis_catalog = [
      {
        key: 'native_ratio',
        label: 'Ratio',
        group: 'Risk',
        source: 'Engine',
        format: 'number',
        description: 'Exact native value',
      },
    ]
    study.rows[0].analysis = { version: 'v1', metrics: { native_ratio: 1.001 }, unavailable: {} }
    study.rows[1].analysis = { version: 'v1', metrics: { native_ratio: 1.002 }, unavailable: {} }
    const rows = sortStudyTrials(studyTrialRows(study, 'distinct'), study, {
      sortKey: 'native_ratio',
      sortDirection: 'desc',
    })
    expect(rows.map((row) => row.number)).toEqual([1, 2, 0])
  })

  it('pages the chosen ordering and clamps stale and invalid pages to real rows', () => {
    const study = makeStudy(Array.from({ length: 61 }, (_, index) => candidate(index, index)))
    const rows = studyTrialRows(study, 'distinct')
    expect(studyTrialPage(rows, 1).rows.map((row) => row.number)).toEqual(
      Array.from({ length: 25 }, (_, i) => i + 25)
    )
    expect(studyTrialPage(rows, 99)).toMatchObject({ page: 2 })
    expect(studyTrialPage(rows, -1)).toMatchObject({ page: 0 })
    expect(studyTrialPage(rows, Number.NaN)).toMatchObject({ page: 0 })
    expect(studyTrialPage([], 1)).toEqual({ page: 0, rows: [] })
  })
})
