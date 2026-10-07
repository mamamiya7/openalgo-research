import type { Data, Layout } from 'plotly.js'
import bar from 'plotly.js/lib/bar'
import box from 'plotly.js/lib/box'
import candlestick from 'plotly.js/lib/candlestick'
import contour from 'plotly.js/lib/contour'
import Plotly from 'plotly.js/lib/core'
import heatmap from 'plotly.js/lib/heatmap'
import histogram from 'plotly.js/lib/histogram'
import parcoords from 'plotly.js/lib/parcoords'
import scatter from 'plotly.js/lib/scatter'
import table from 'plotly.js/lib/table'
import { useMemo } from 'react'
import createPlotlyComponent from 'react-plotly.js/factory'
import type { AnalysisChart } from '@/api/portfolioResearch'
import { useThemeStore } from '@/stores/themeStore'

// This module is loaded only when an analysis chart is opened.
Plotly.register([scatter, bar, candlestick, box, contour, heatmap, histogram, parcoords, table])
const factory = createPlotlyComponent as any
const Plot = (factory.default ?? factory)(Plotly)

export default function ResearchPlot({
  chart,
  parameterLabels,
  onPoint,
}: {
  chart: AnalysisChart
  parameterLabels?: Record<string, string>
  onPoint?: (point: { x?: unknown; y?: unknown; z?: unknown; customdata?: unknown }) => void
}) {
  const { mode, appMode } = useThemeStore()
  const dark = mode === 'dark' || appMode === 'analyzer'
  const figure = useMemo(() => {
    // Plotly mutates figure objects. Keep the saved evidence immutable in React too.
    const copy = JSON.parse(JSON.stringify(chart.figure ?? { data: [], layout: {} }))
    const studyChart = [
      'history',
      'importance',
      'slice',
      'contour',
      'parallel',
      'rank',
      'edf',
      'timeline',
    ].includes(chart.id)
    const labels = Object.entries(parameterLabels ?? {}).sort(([a], [b]) => b.length - a.length)
    const displayLabels = (value: unknown): unknown => {
      if (typeof value === 'string') {
        let text = studyChart
          ? value.replace(
              /(\bTrial(?:\s*[:#])?\s*)(\d+)\b/g,
              (_, prefix, number) => `${prefix}${Number(number) + 1}`
            )
          : value
        for (const [key, label] of labels) text = text.replaceAll(key, label)
        return text
      }
      if (Array.isArray(value)) return value.map(displayLabels)
      if (value && typeof value === 'object') {
        return Object.fromEntries(
          Object.entries(value).map(([key, item]) => [key, displayLabels(item)])
        )
      }
      return value
    }
    // Native Optuna records use zero-based IDs; the app's trial labels are one-based.
    // This changes display coordinates/text only, never the stored study or objective values.
    copy.data = displayLabels(copy.data)
    copy.layout = displayLabels(copy.layout)
    if (chart.id === 'history') {
      for (const trace of copy.data) {
        if (Array.isArray(trace.x))
          trace.x = trace.x.map((value: unknown) =>
            typeof value === 'number' && Number.isInteger(value) ? value + 1 : value
          )
      }
    }
    if (chart.id === 'slice') {
      for (const trace of copy.data) {
        // Native slice colors encode the trial number, so their colorbar must use
        // the same one-based labels as history, tables and candidate dialogs.
        if (Array.isArray(trace.marker?.color))
          trace.marker.color = trace.marker.color.map((value: unknown) =>
            typeof value === 'number' && Number.isInteger(value) ? value + 1 : value
          )
      }
    }
    const color = dark ? '#e4e4e7' : '#27272a'
    const gridcolor = dark ? '#303036' : '#e4e4e7'
    const layout: Record<string, any> = {
      ...copy.layout,
      template: {},
      autosize: true,
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      font: { ...copy.layout.font, family: 'Inter, system-ui, sans-serif', color },
      margin: { l: 64, r: 30, t: 48, b: 64, ...copy.layout.margin },
      hoverlabel: { bgcolor: dark ? '#18181b' : '#ffffff', font: { color } },
      xaxis: copy.layout.xaxis ?? {},
      yaxis: copy.layout.yaxis ?? {},
      uirevision: chart.id,
    }
    delete layout.width
    delete layout.height
    if (chart.id === 'monthly-returns') {
      const months =
        copy.data.find((trace: Record<string, unknown>) => trace.type === 'heatmap')?.x ?? []
      const labels = [
        'Jan',
        'Feb',
        'Mar',
        'Apr',
        'May',
        'Jun',
        'Jul',
        'Aug',
        'Sep',
        'Oct',
        'Nov',
        'Dec',
      ]
      layout.xaxis = {
        ...layout.xaxis,
        type: 'category',
        tickmode: 'array',
        tickvals: months,
        ticktext: months.map(
          (month: string | number) => labels[Number(month) - 1] ?? String(month)
        ),
      }
      layout.yaxis = { ...layout.yaxis, type: 'category' }
      for (const trace of copy.data) {
        if (trace.type === 'heatmap') {
          trace.colorscale = [
            [0, '#ef4444'],
            [0.5, dark ? '#27272a' : '#f4f4f5'],
            [1, '#22c55e'],
          ]
          trace.zmid = 0
        }
      }
    }
    if (chart.id === 'yearly-returns') layout.xaxis = { ...layout.xaxis, type: 'category' }
    const normalizeColorbar = (holder: Record<string, any> | undefined) => {
      if (holder?.colorbar && typeof holder.colorbar.title === 'string') {
        holder.colorbar.title = { text: holder.colorbar.title }
      }
    }
    for (const key of Object.keys(layout)) {
      if (/^[xy]axis\d*$/.test(key)) {
        layout[key] = {
          ...layout[key],
          gridcolor,
          zerolinecolor: gridcolor,
          color,
          automargin: true,
        }
        if (typeof layout[key].title === 'string') layout[key].title = { text: layout[key].title }
      }
      if (/^coloraxis\d*$/.test(key)) normalizeColorbar(layout[key])
    }
    for (const trace of copy.data) {
      normalizeColorbar(trace)
      normalizeColorbar(trace.marker)
      if (trace.type === 'table') {
        trace.header = {
          ...trace.header,
          fill: { color: dark ? '#27272a' : '#f4f4f5' },
          font: { ...trace.header?.font, color },
        }
        trace.cells = {
          ...trace.cells,
          fill: { color: dark ? '#18181b' : '#ffffff' },
          font: { ...trace.cells?.font, color },
        }
      }
    }
    return { data: copy.data as Data[], layout: layout as Partial<Layout> }
  }, [chart, dark, parameterLabels])
  return (
    <Plot
      key={chart.id}
      data={figure.data}
      layout={figure.layout}
      config={{
        responsive: true,
        displaylogo: false,
        scrollZoom: false,
        toImageButtonOptions: { format: 'png', filename: `research-${chart.id}`, scale: 2 },
      }}
      useResizeHandler
      onClick={
        onPoint
          ? (event: {
              points?: { x?: unknown; y?: unknown; z?: unknown; customdata?: unknown }[]
            }) => {
              if (event.points?.[0]) onPoint(event.points[0])
            }
          : undefined
      }
      style={{ width: '100%', height: '100%' }}
    />
  )
}
