import type { AnalysisChart } from '@/api/portfolioResearch'

// Report views share colors and axes; saved figures remain immutable.
export function reportChartView(chart: AnalysisChart, log = false): AnalysisChart {
  if (!chart.figure) return chart
  const equity = chart.id.startsWith('account-equity')
  const traces = equity
    ? chart.figure.data.filter((trace) => trace.name !== 'Cash')
    : chart.figure.data
  const loss = chart.id.startsWith('account-underwater')
  return {
    ...chart,
    ...(equity ? { title: 'Account equity' } : {}),
    figure: {
      data: traces.map((trace) => ({
        ...trace,
        ...(trace.type === 'scatter' && trace.mode !== 'markers'
          ? {
              line: { ...(trace.line as object), color: loss ? '#ef4444' : '#10b981', width: 2 },
              ...(loss ? { fillcolor: 'rgba(239,68,68,0.12)' } : {}),
            }
          : {}),
      })),
      layout: {
        ...chart.figure.layout,
        title: undefined,
        showlegend: false,
        margin: { l: 48, r: 16, t: 12, b: 42 },
        ...(log ? { yaxis: { ...(chart.figure.layout.yaxis as object), type: 'log' } } : {}),
      },
    },
  }
}
