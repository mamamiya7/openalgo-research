import type { AnalysisChart, PortfolioResult } from '@/api/portfolioResearch'

type Experiment = NonNullable<PortfolioResult['experiment']>
export interface StudyPoint {
  configId: string
  proposalNumber: number
}
const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)

export function studyPoint(
  experiment: Experiment,
  configId: string,
  number?: number
): StudyPoint | null {
  const row = experiment.rows.find((item) => item.config_id === configId)
  if (!row) return null
  if (number == null || number === row.trial_number)
    return { configId, proposalNumber: row.trial_number }
  const proposal = experiment.trials?.find(
    (item) => item.number === number && item.config_id === configId && item.state === 'complete'
  )
  return proposal ? { configId, proposalNumber: proposal.number } : null
}

export function pointsFromChart(experiment: Experiment, customdata: unknown): StudyPoint[] {
  if (!customdata || typeof customdata !== 'object') return []
  const data = customdata as { research_trials?: unknown; research_config?: unknown }
  if (typeof data.research_config === 'string') {
    const point = studyPoint(experiment, data.research_config)
    return point ? [point] : []
  }
  if (!Array.isArray(data.research_trials)) return []
  return data.research_trials.flatMap((number) => {
    const trial = experiment.trials?.find(
      (item) => item.number === number && item.state === 'complete'
    )
    const point = trial && studyPoint(experiment, trial.config_id, trial.number)
    return point ? [point] : []
  })
}

/** Add interaction to a copy of native figures. Never infer identity from Plotly point order.
 * Contour observations may overlap: preserve every matching proposal for an explicit choice.
 * Background interpolation, best-so-far lines and aggregate plots remain non-interactive. */
export function connectedStudyChart(chart: AnalysisChart, experiment: Experiment): AnalysisChart {
  if (!chart.figure || !experiment.trials?.length) return chart
  const copy: AnalysisChart = JSON.parse(JSON.stringify(chart))
  const figure = copy.figure!
  // The surrounding section supplies the title. Keep native values/curves but
  // shorten repeated objective text so it does not squeeze the plot on phones.
  delete figure.layout.title
  for (const [key, value] of Object.entries(figure.layout)) {
    if (/^[xy]axis\d*$/.test(key) && value && typeof value === 'object') {
      const axis = value as { title?: { text?: string } }
      if (axis.title?.text === experiment.optimizer.objective_definition)
        axis.title = { ...axis.title, text: 'Objective score' }
    }
  }
  if (chart.id === 'history') {
    figure.layout.legend = {
      orientation: 'h',
      x: 0,
      y: 1.04,
      xanchor: 'left',
      yanchor: 'bottom',
      font: { size: 11 },
    }
    for (const trace of figure.data) {
      if (trace.name === experiment.optimizer.objective_definition) trace.name = 'Trial score'
      if (trace.name === 'Best Value') trace.name = 'Best so far'
    }
  }
  for (const trace of figure.data) {
    const colorbar = trace.colorbar as { title?: { text?: string } } | undefined
    if (colorbar?.title?.text === experiment.optimizer.objective_definition)
      colorbar.title = { ...colorbar.title, text: 'Score' }
  }
  const trials = experiment.trials.filter((trial) => trial.state === 'complete')
  for (const trace of figure.data) {
    if (trace.type !== 'scatter' || trace.mode !== 'markers' || !Array.isArray(trace.x)) continue
    const x = trace.x
    const y = Array.isArray(trace.y) ? trace.y : []
    const marker = trace.marker as Record<string, unknown> | undefined
    const colors = Array.isArray(marker?.color) ? marker.color : []
    const hovertext = Array.isArray(trace.hovertext) ? trace.hovertext : []
    const axisName = (axis: unknown, prefix: 'x' | 'y') => {
      const suffix = typeof axis === 'string' ? axis.slice(1) : ''
      const layout = figure.layout[`${prefix}axis${suffix}`] as
        | { title?: { text?: string } }
        | undefined
      return layout?.title?.text
    }
    trace.customdata = x.map((value, index) => {
      let matches: number[] = []
      if (chart.id === 'history') {
        matches = trials
          .filter((trial) => trial.number === value && trial.value === y[index])
          .map((trial) => trial.number)
      } else if (chart.id === 'slice') {
        const parameter = axisName(trace.xaxis, 'x')
        matches = trials
          .filter(
            (trial) =>
              trial.number === colors[index] &&
              parameter &&
              trial.params[parameter] === value &&
              trial.value === y[index]
          )
          .map((trial) => trial.number)
      } else if (chart.id === 'contour') {
        const xKey = axisName(trace.xaxis, 'x')
        const yKey = axisName(trace.yaxis, 'y')
        matches = trials
          .filter(
            (trial) =>
              xKey && yKey && trial.params[xKey] === value && trial.params[yKey] === y[index]
          )
          .map((trial) => trial.number)
      } else if (chart.id === 'rank' && typeof hovertext[index] === 'string') {
        const match = /^Trial #(\d+)<br>/.exec(hovertext[index])
        const xKey = axisName(trace.xaxis, 'x')
        const yKey = axisName(trace.yaxis, 'y')
        matches = trials
          .filter(
            (trial) =>
              match &&
              trial.number === Number(match[1]) &&
              xKey &&
              yKey &&
              trial.params[xKey] === value &&
              trial.params[yKey] === y[index]
          )
          .map((trial) => trial.number)
      }
      return { research_trials: matches }
    })
  }
  return copy
}

export function returnDrawdownChart(experiment: Experiment): AnalysisChart {
  const rows = experiment.rows.filter(
    (row) => finite(row.summary.net_return_pct) && finite(row.summary.max_drawdown_pct)
  )
  return {
    id: 'study-return-drawdown',
    title: 'Return and drawdown',
    status: rows.length ? 'available' : 'unavailable',
    reason: rows.length ? undefined : 'Return and drawdown were not recorded for these portfolios.',
    figure: {
      data: [
        {
          type: 'scatter',
          mode: 'markers',
          x: rows.map((row) => row.summary.max_drawdown_pct),
          y: rows.map((row) => row.summary.net_return_pct),
          customdata: rows.map((row) => ({ research_config: row.config_id })),
          text: rows.map(
            (row) =>
              `Trial ${row.trial_number + 1}${row.config_id === experiment.recommendation_id ? ' · Best by objective' : ''}`
          ),
          hovertemplate: '%{text}<br>Return %{y:.2f}%<br>Drawdown %{x:.2f}%<extra></extra>',
          marker: {
            size: rows.map((row) => (row.config_id === experiment.recommendation_id ? 13 : 8)),
            color: rows.map((row) =>
              row.config_id === experiment.recommendation_id ? '#22c55e' : '#60a5fa'
            ),
            opacity: 0.8,
          },
        },
      ],
      layout: {
        xaxis: { title: { text: 'Max drawdown (%)' } },
        yaxis: { title: { text: 'Return (%)' } },
        showlegend: false,
      },
    },
  }
}

export function studyCounts(experiment: Experiment) {
  const records = experiment.trials
  return {
    proposed: records?.length ?? experiment.counts.proposed ?? null,
    distinct: new Set(experiment.rows.map((row) => row.config_id)).size,
    reused: records
      ? records.filter((row) => row.reused && row.state === 'complete').length
      : (experiment.counts.reused_trials ?? null),
    rejected: records
      ? records.filter((row) => row.state === 'pruned').length
      : (experiment.counts.rejected_allocations ?? null),
  }
}

const axisLabels: Record<string, string> = {
  target_pct: 'Profit target',
  stop_pct: 'Stop loss',
  hold_sessions: 'Holding sessions',
  hold_minutes: 'Holding minutes',
  trailing_pct: 'Trailing stop',
  order_size_pct: 'Per-trade size',
  allocation_pct: 'Allocation',
}
export function studyParameterLabels(result: PortfolioResult) {
  return Object.fromEntries(
    Object.keys(result.experiment?.search_space?.axes ?? {}).map((key) => {
      const [id, parameter] = key.split('.')
      const name = result.strategies.find((strategy) => strategy.id === id)?.name ?? id
      return [
        key,
        `${axisLabels[parameter] ?? parameter}${result.strategies.length > 1 ? ` · ${name}` : ''}`,
      ]
    })
  )
}
