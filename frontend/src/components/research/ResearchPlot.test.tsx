import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnalysisChart } from '@/api/portfolioResearch'
import { useThemeStore } from '@/stores/themeStore'
import ResearchPlot from './ResearchPlot'

const observed = vi.hoisted(() => ({ plot: vi.fn() }))
vi.mock('plotly.js/lib/core', () => ({ default: { register: vi.fn() } }))
vi.mock('plotly.js/lib/bar', () => ({ default: {} }))
vi.mock('plotly.js/lib/box', () => ({ default: {} }))
vi.mock('plotly.js/lib/candlestick', () => ({ default: {} }))
vi.mock('plotly.js/lib/contour', () => ({ default: {} }))
vi.mock('plotly.js/lib/heatmap', () => ({ default: {} }))
vi.mock('plotly.js/lib/histogram', () => ({ default: {} }))
vi.mock('plotly.js/lib/parcoords', () => ({ default: {} }))
vi.mock('plotly.js/lib/scatter', () => ({ default: {} }))
vi.mock('plotly.js/lib/table', () => ({ default: {} }))
vi.mock('react-plotly.js/factory', () => ({
  default: () => (props: unknown) => {
    observed.plot(props)
    return <div />
  },
}))
beforeEach(() => {
  observed.plot.mockClear()
  useThemeStore.setState({ mode: 'dark', appMode: 'live' })
})

describe('research chart presentation', () => {
  it('labels native slice colors from one without changing candidate identifiers', () => {
    const chart: AnalysisChart = {
      id: 'slice',
      title: 'Slice',
      status: 'available',
      figure: {
        data: [
          {
            type: 'scatter',
            mode: 'markers',
            x: [1, 2],
            y: [5, 6],
            marker: { color: [0, 1] },
            customdata: [{ research_trials: [0] }, { research_trials: [1] }],
          },
        ],
        layout: {},
      },
    }
    const saved = JSON.stringify(chart)
    render(<ResearchPlot chart={chart} />)
    const props = observed.plot.mock.lastCall![0]
    expect(props.data[0].marker.color).toEqual([1, 2])
    expect(props.data[0].customdata).toEqual([{ research_trials: [0] }, { research_trials: [1] }])
    expect(JSON.stringify(chart)).toBe(saved)
  })
  it('forwards original heatmap coordinates without changing the saved evidence', () => {
    const chart: AnalysisChart = {
      id: 'monthly-returns',
      title: 'Monthly returns',
      status: 'available',
      figure: {
        data: [{ type: 'heatmap', x: ['01'], y: ['2026'], z: [[0]] }],
        layout: {},
      },
    }
    const saved = JSON.stringify(chart)
    const onPoint = vi.fn()
    render(<ResearchPlot chart={chart} onPoint={onPoint} />)
    const props = observed.plot.mock.lastCall![0]
    const point = { x: '01', y: '2026', z: 0, customdata: 'saved-cell' }
    props.onClick({ points: [point] })
    expect(onPoint).toHaveBeenCalledExactlyOnceWith(point)
    props.onClick({ points: [] })
    expect(onPoint).toHaveBeenCalledOnce()
    expect(JSON.stringify(chart)).toBe(saved)
  })

  it('keeps calendar labels categorical and uses a centered return colorscale without editing saved figures', () => {
    const chart: AnalysisChart = {
      id: 'monthly-returns',
      title: 'Monthly returns',
      status: 'available',
      figure: {
        data: [
          {
            type: 'heatmap',
            x: ['01', '02'],
            y: ['2026'],
            z: [[1, -1]],
            colorscale: 'RdYlGn',
            colorbar: { title: '%' },
          },
        ],
        layout: {},
      },
    }
    const saved = JSON.stringify(chart)
    render(<ResearchPlot chart={chart} />)
    const props = observed.plot.mock.lastCall![0]
    expect(props.layout.xaxis).toMatchObject({
      type: 'category',
      tickvals: ['01', '02'],
      ticktext: ['Jan', 'Feb'],
      gridcolor: '#303036',
    })
    expect(props.layout.yaxis).toMatchObject({ type: 'category', gridcolor: '#303036' })
    expect(props.data[0]).toMatchObject({
      zmid: 0,
      colorscale: [
        [0, '#ef4444'],
        [0.5, '#27272a'],
        [1, '#22c55e'],
      ],
      colorbar: { title: { text: '%' } },
    })
    expect(JSON.stringify(chart)).toBe(saved)
  })

  it('prevents fractional year labels and themes axes omitted by the saved figure', () => {
    const chart: AnalysisChart = {
      id: 'yearly-returns',
      title: 'Yearly returns',
      status: 'available',
      figure: { data: [{ type: 'bar', x: ['2026'], y: [1] }], layout: {} },
    }
    render(<ResearchPlot chart={chart} />)
    const props = observed.plot.mock.lastCall![0]
    expect(props.layout.xaxis.type).toBe('category')
    expect(props.layout.xaxis.gridcolor).toBe('#303036')
    expect(props.layout.yaxis.gridcolor).toBe('#303036')
  })

  it('uses readable parameter labels and matching trial numbers without changing native evidence', () => {
    const key = '8bca1234.stop_pct'
    const chart: AnalysisChart = {
      id: 'importance',
      title: 'Importance',
      status: 'available',
      figure: {
        data: [
          {
            type: 'bar',
            y: [key],
            x: [0.4],
            hovertext: [`Trial #0: ${key}`],
            dimensions: [{ label: key, values: [0.4] }],
          },
        ],
        layout: { yaxis: { title: { text: key } } },
      },
    }
    const saved = JSON.stringify(chart)
    const { rerender } = render(
      <ResearchPlot chart={chart} parameterLabels={{ [key]: 'Stop loss' }} />
    )
    let props = observed.plot.mock.lastCall![0]
    expect(props.data[0].y).toEqual(['Stop loss'])
    expect(props.data[0].hovertext).toEqual(['Trial #1: Stop loss'])
    expect(props.data[0].dimensions[0].label).toBe('Stop loss')
    expect(props.layout.yaxis).toMatchObject({ title: { text: 'Stop loss' }, automargin: true })
    expect(props.data[0].x).toEqual([0.4])
    expect(JSON.stringify(chart)).toBe(saved)

    const history: AnalysisChart = {
      id: 'history',
      title: 'History',
      status: 'available',
      figure: {
        data: [{ type: 'scatter', x: [0, 4], y: [12, 7], text: ['Trial 0', 'Trial: 4'] }],
        layout: {},
      },
    }
    rerender(<ResearchPlot chart={history} />)
    props = observed.plot.mock.lastCall![0]
    expect(props.data[0].x).toEqual([1, 5])
    expect(props.data[0].y).toEqual([12, 7])
    expect(props.data[0].text).toEqual(['Trial 1', 'Trial: 5'])
    expect(history.figure!.data[0].x).toEqual([0, 4])

    rerender(
      <ResearchPlot
        chart={{
          ...history,
          id: 'timeline',
          figure: { data: [{ type: 'bar', x: [600, 900], y: ['Trial 0', 'Trial 4'] }], layout: {} },
        }}
      />
    )
    props = observed.plot.mock.lastCall![0]
    expect(props.data[0].y).toEqual(['Trial 1', 'Trial 5'])
    expect(props.data[0].x).toEqual([600, 900])
  })
})
