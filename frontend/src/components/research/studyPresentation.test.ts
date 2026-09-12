import { describe, expect, it } from 'vitest'
import type { AnalysisChart, PortfolioResult, PortfolioTrial } from '@/api/portfolioResearch'
import {
  connectedStudyChart,
  pointsFromChart,
  returnDrawdownChart,
  studyCounts,
  studyParameterLabels,
  studyPoint,
} from './studyPresentation'

type Experiment = NonNullable<PortfolioResult['experiment']>

it('presents native timeline trial numbers consistently without changing saved figures', () => {
  const original = chart(
    'timeline',
    [
      {
        type: 'bar',
        orientation: 'h',
        name: 'COMPLETE',
        y: [3, 8, 12],
        x: [10, 20, 30],
        base: ['a', 'b', 'c'],
        text: ['raw', 'raw', 'raw'],
      },
      { type: 'bar', orientation: 'h', name: 'PRUNED', y: [6], x: [5], base: ['d'], text: ['raw'] },
    ],
    { yaxis: { title: { text: 'Trial' } } }
  )
  const before = JSON.stringify(original)
  const shown = connectedStudyChart(original, study())
  expect(shown.figure!.data[0].y).toEqual([4, 9, 13])
  expect(shown.figure!.data[0].text).toEqual([
    'Trial 4<br>Finished<br>Objective score: 2',
    'Trial 9<br>Finished<br>Objective score: 0',
    'Trial 13<br>Finished<br>Objective score: 2',
  ])
  expect(shown.figure!.data[1].text).toEqual(['Trial 7<br>Allocation excluded'])
  expect(shown.figure!.layout.yaxis).toMatchObject({
    tickvals: [4, 7, 9, 13],
    ticktext: ['4', '7', '9', '13'],
  })
  expect(shown.figure!.data[0].x).toEqual([10, 20, 30])
  expect(shown.figure!.data[0].base).toEqual(['a', 'b', 'c'])
  expect(JSON.stringify(original)).toBe(before)
})

it('keeps an unverified native timeline intact rather than renumbering only some bars', () => {
  const original = chart('timeline', [
    { type: 'bar', orientation: 'h', y: [3, 99], text: ['raw', 'unknown'] },
  ])
  expect(connectedStudyChart(original, study()).figure!.data).toEqual(original.figure!.data)
})
function candidate(number: number, configId: string): PortfolioTrial {
  return {
    config_id: configId,
    trial_number: number,
    score: 2,
    stage: 'tpe',
    strategies: [],
    summary: { net_return_pct: 10, max_drawdown_pct: 4 },
  }
}
function study(): Experiment {
  return {
    kind: 'portfolio_optimize',
    rows: [candidate(3, 'a'), candidate(8, 'b')],
    recommendation_id: 'a',
    selected_strategies: [],
    counts: { proposed: 99, reused_trials: 77, rejected_allocations: 22, evaluated_this_pass: 55 },
    optimizer: { sampler: 'TPE', version: '1', objective_definition: 'Return minus drawdown' },
    specification: { sampler: 'tpe', trials: 100, objective: 'balanced', seed: 0 },
    trials: [
      {
        number: 3,
        config_id: 'a',
        state: 'complete',
        value: 2,
        reused: false,
        params: { 's.target_pct': 5, 's.stop_pct': 3 },
      },
      {
        number: 6,
        config_id: 'rejected',
        state: 'pruned',
        value: null,
        reused: false,
        params: { 's.target_pct': 5, 's.stop_pct': 3 },
      },
      {
        number: 8,
        config_id: 'b',
        state: 'complete',
        value: 0,
        reused: false,
        params: { 's.target_pct': 10, 's.stop_pct': 4 },
      },
      {
        number: 12,
        config_id: 'a',
        state: 'complete',
        value: 2,
        reused: true,
        params: { 's.target_pct': 5, 's.stop_pct': 3 },
      },
    ],
  }
}
const chart = (
  id: string,
  data: Array<Record<string, unknown>>,
  layout: Record<string, unknown> = {}
): AnalysisChart => ({
  id,
  title: id,
  status: 'available',
  figure: { data, layout },
})
const interactions = (value: AnalysisChart, trace = 0) =>
  value.figure!.data[trace].customdata as unknown[]

function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    for (const item of Object.values(value)) freeze(item)
    Object.freeze(value)
  }
  return value
}

describe('native study evidence presentation', () => {
  it('connects only actual history markers with matching stored number and score, not plot order or the best line', () => {
    const evidence = study()
    const original = chart('history', [
      {
        type: 'scatter',
        mode: 'markers',
        x: [12, 3, 8, 0, 6, 3],
        y: [2, 2, 0, 2, null, 99],
        name: 'Objective Value',
      },
      { type: 'scatter', mode: 'lines', x: [3, 8, 12], y: [2, 2, 2], name: 'Best Value' },
    ])
    const connected = connectedStudyChart(original, evidence)
    expect(interactions(connected).map((data) => pointsFromChart(evidence, data))).toEqual([
      [{ configId: 'a', proposalNumber: 12 }],
      [{ configId: 'a', proposalNumber: 3 }],
      [{ configId: 'b', proposalNumber: 8 }],
      [],
      [],
      [],
    ])
    expect(connected.figure!.data[1].customdata).toBeUndefined()
  })

  it('validates native slice trial-color identity, parameter axis, parameter value and score together', () => {
    const evidence = study()
    const original = chart(
      'slice',
      [
        {
          type: 'scatter',
          mode: 'markers',
          xaxis: 'x2',
          x: [5, 10, 5, 999, 5],
          y: [2, 0, 99, 2, 2],
          marker: { color: [12, 8, 3, 3, 99] },
        },
        { type: 'scatter', mode: 'markers', xaxis: 'x3', x: [5], y: [2], marker: { color: [3] } },
      ],
      { xaxis2: { title: { text: 's.target_pct' } }, xaxis3: { title: { text: 'unknown-axis' } } }
    )
    const connected = connectedStudyChart(original, evidence)
    expect(interactions(connected).map((data) => pointsFromChart(evidence, data))).toEqual([
      [{ configId: 'a', proposalNumber: 12 }],
      [{ configId: 'b', proposalNumber: 8 }],
      [],
      [],
      [],
    ])
    expect(pointsFromChart(evidence, interactions(connected, 1)[0])).toEqual([])
  })

  it('preserves the explicit set of overlapping contour observations while leaving interpolation unclickable', () => {
    const evidence = study()
    const original = chart(
      'contour',
      [
        { type: 'contour', x: [5, 7.5, 10], y: [3, 3.5, 4], z: [[2, 1, 0]] },
        { type: 'scatter', mode: 'markers', x: [5, 7.5, 10], y: [3, 3.5, 4] },
      ],
      { xaxis: { title: { text: 's.target_pct' } }, yaxis: { title: { text: 's.stop_pct' } } }
    )
    const connected = connectedStudyChart(original, evidence)
    expect(connected.figure!.data[0].customdata).toBeUndefined()
    expect(pointsFromChart(evidence, interactions(connected, 1)[0])).toEqual([
      { configId: 'a', proposalNumber: 3 },
      { configId: 'a', proposalNumber: 12 },
    ])
    expect(pointsFromChart(evidence, interactions(connected, 1)[1])).toEqual([])
    expect(pointsFromChart(evidence, interactions(connected, 1)[2])).toEqual([
      { configId: 'b', proposalNumber: 8 },
    ])
  })

  it('validates native rank hovertext identity against both parameter axes', () => {
    const evidence = study()
    const original = chart(
      'rank',
      [
        {
          type: 'scatter',
          mode: 'markers',
          xaxis: 'x2',
          yaxis: 'y2',
          x: [5, 10, 5, 5, 5, 10],
          y: [3, 4, 3, 4, 3, 4],
          hovertext: [
            'Trial #12<br>Value: 2',
            'Trial #8<br>Value: 0',
            'Trial #99<br>Value: 2',
            'Trial #3<br>Value: 2',
            'not a native identity',
            'Trial #3<br>Value: 2',
          ],
        },
      ],
      { xaxis2: { title: { text: 's.target_pct' } }, yaxis2: { title: { text: 's.stop_pct' } } }
    )
    const connected = connectedStudyChart(original, evidence)
    expect(interactions(connected).map((data) => pointsFromChart(evidence, data))).toEqual([
      [{ configId: 'a', proposalNumber: 12 }],
      [{ configId: 'b', proposalNumber: 8 }],
      [],
      [],
      [],
      [],
    ])
  })

  it('keeps missing raw proposal history, aggregate traces and unavailable charts non-clickable', () => {
    const evidence = study()
    delete evidence.trials
    const history = chart('history', [{ type: 'scatter', mode: 'markers', x: [3], y: [2] }])
    expect(connectedStudyChart(history, evidence)).toBe(history)
    expect(connectedStudyChart(history, evidence).figure!.data[0].customdata).toBeUndefined()
    expect(pointsFromChart(evidence, { research_trials: [3] })).toEqual([])
    const unknown = chart('edf', [{ type: 'scatter', mode: 'markers', x: [3], y: [2] }])
    expect(
      pointsFromChart(study(), interactions(connectedStudyChart(unknown, study()))[0])
    ).toEqual([])
    const absent: AnalysisChart = {
      id: 'slice',
      title: 'Slice',
      status: 'unavailable',
      reason: 'No varied parameter.',
    }
    expect(connectedStudyChart(absent, study())).toBe(absent)
  })

  it('adds identity only to a copy and preserves saved scientific figure data and study evidence', () => {
    const evidence = freeze(study())
    const original = freeze(
      chart(
        'history',
        [
          {
            type: 'scatter',
            mode: 'markers',
            x: [3, 8],
            y: [2, 0],
            marker: { size: [7, 9], color: ['red', 'green'] },
          },
        ],
        { title: { text: 'Native history' } }
      )
    )
    const receipt = JSON.stringify({ original, evidence })
    const connected = connectedStudyChart(original, evidence)
    expect(connected).not.toBe(original)
    expect(connected.figure).not.toBe(original.figure)
    expect(connected.figure!.data[0].x).toEqual([3, 8])
    expect(connected.figure!.data[0].y).toEqual([2, 0])
    expect(connected.figure!.data[0].marker).toEqual(original.figure!.data[0].marker)
    expect(connected.figure!.layout.title).toBeUndefined()
    expect(connected.figure!.layout.legend).toMatchObject({ orientation: 'h', x: 0 })
    expect(JSON.stringify({ original, evidence })).toBe(receipt)
  })

  it('validates configuration identity and original or reused proposal numbers instead of falling back to an unrelated candidate', () => {
    const evidence = study()
    expect(studyPoint(evidence, 'a')).toEqual({ configId: 'a', proposalNumber: 3 })
    expect(studyPoint(evidence, 'a', 3)).toEqual({ configId: 'a', proposalNumber: 3 })
    expect(studyPoint(evidence, 'a', 12)).toEqual({ configId: 'a', proposalNumber: 12 })
    expect(studyPoint(evidence, 'a', 8)).toBeNull()
    expect(studyPoint(evidence, 'rejected', 6)).toBeNull()
    expect(studyPoint(evidence, 'unknown', 3)).toBeNull()
    expect(pointsFromChart(evidence, { research_trials: [6, 12, 99, '3'] })).toEqual([
      { configId: 'a', proposalNumber: 12 },
    ])
    for (const invalid of [
      null,
      '3',
      3,
      [],
      {},
      { research_trials: '3' },
      { research_config: 'unknown' },
    ]) {
      expect(pointsFromChart(evidence, invalid)).toEqual([])
    }
  })

  it('plots saved return/drawdown pairs including zero, excludes missing/nonfinite values and links configuration identity', () => {
    const evidence = study()
    evidence.rows.push(candidate(13, 'c'), candidate(14, 'd'), candidate(15, 'e'))
    evidence.rows[0].summary = { net_return_pct: 0, max_drawdown_pct: 0 }
    evidence.rows[1].summary = { net_return_pct: -2, max_drawdown_pct: 5 }
    evidence.rows[2].summary = { net_return_pct: null, max_drawdown_pct: 4 }
    evidence.rows[3].summary = { net_return_pct: 2, max_drawdown_pct: Number.NaN }
    evidence.rows[4].summary = { net_return_pct: Number.POSITIVE_INFINITY, max_drawdown_pct: 4 }
    const original = JSON.stringify(evidence)
    const scatter = returnDrawdownChart(evidence)
    expect(scatter.status).toBe('available')
    expect(scatter.figure!.data[0]).toMatchObject({
      x: [0, 5],
      y: [0, -2],
      text: ['Trial 4 · Best by objective', 'Trial 9'],
    })
    expect(interactions(scatter).map((data) => pointsFromChart(evidence, data))).toEqual([
      [{ configId: 'a', proposalNumber: 3 }],
      [{ configId: 'b', proposalNumber: 8 }],
    ])
    expect(JSON.stringify(evidence)).toBe(original)
    evidence.rows = evidence.rows.slice(2)
    expect(returnDrawdownChart(evidence)).toMatchObject({
      status: 'unavailable',
      reason: 'Return and drawdown were not recorded for these portfolios.',
    })
  })

  it('counts actual recorded proposals and unique configurations, falling back only to saved counters when history is absent', () => {
    const evidence = study()
    evidence.rows.push({ ...evidence.rows[0] })
    expect(studyCounts(evidence)).toEqual({ proposed: 4, distinct: 2, reused: 1, rejected: 1 })
    evidence.trials = []
    expect(studyCounts(evidence)).toEqual({ proposed: 0, distinct: 2, reused: 0, rejected: 0 })
    delete evidence.trials
    expect(studyCounts(evidence)).toEqual({ proposed: 99, distinct: 2, reused: 77, rejected: 22 })
    evidence.counts = { proposed: 0, reused_trials: 0, rejected_allocations: 0 }
    expect(studyCounts(evidence)).toEqual({ proposed: 0, distinct: 2, reused: 0, rejected: 0 })
    evidence.counts = { evaluated_this_pass: 100 }
    expect(studyCounts(evidence)).toEqual({
      proposed: null,
      distinct: 2,
      reused: null,
      rejected: null,
    })
  })

  it('uses friendly parameter names with strategy names only where needed for disambiguation', () => {
    const result = {
      strategies: [
        { id: 's', name: 'Breakout' },
        { id: 't', name: 'Reversal' },
      ],
      experiment: {
        search_space: {
          axes: {
            's.target_pct': {},
            't.stop_pct': {},
            's.hold_minutes': {},
            's.future_setting': {},
          },
        },
      },
    } as unknown as PortfolioResult
    expect(studyParameterLabels(result)).toEqual({
      's.target_pct': 'Profit target · Breakout',
      't.stop_pct': 'Stop loss · Reversal',
      's.hold_minutes': 'Holding minutes · Breakout',
      's.future_setting': 'future_setting · Breakout',
    })
    result.strategies = result.strategies.slice(0, 1)
    expect(studyParameterLabels(result)['s.target_pct']).toBe('Profit target')
  })
})
