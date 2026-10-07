import { Component, lazy, type ReactNode, Suspense, useEffect, useRef, useState } from 'react'
import type { AnalysisChart } from '@/api/portfolioResearch'
import { Button } from '@/components/ui/button'

const ResearchPlot = lazy(() => import('./ResearchPlot'))

// A report may contain many native figures. Mount lower figures near the viewport;
// disconnect the observer on entry/unmount and keep no per-report global registry.
export function AnalysisFigure({
  chart,
  height = 300,
  deferred = false,
  parameterLabels,
  onPoint,
}: {
  chart: AnalysisChart
  height?: number
  deferred?: boolean
  parameterLabels?: Record<string, string>
  onPoint?: (point: { x?: unknown; y?: unknown; z?: unknown; customdata?: unknown }) => void
}) {
  const ref = useRef<HTMLElement>(null)
  const [visible, setVisible] = useState(!deferred || typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    if (visible || !ref.current) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: '240px' }
    )
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [visible])
  return (
    <figure ref={ref} className="min-w-0" style={{ height }} aria-label={chart.title}>
      {visible && (
        <PlotBoundary key={chart.id}>
          <Suspense
            fallback={
              <output className="block p-6 text-xs text-muted-foreground">Loading chart…</output>
            }
          >
            <ResearchPlot chart={chart} parameterLabels={parameterLabels} onPoint={onPoint} />
          </Suspense>
        </PlotBoundary>
      )}
    </figure>
  )
}

class PlotBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    return this.state.failed ? (
      <div className="space-y-3 p-8 text-center text-sm text-muted-foreground" aria-live="polite">
        <p>The chart could not be displayed.</p>
        <Button type="button" variant="outline" onClick={() => this.setState({ failed: false })}>
          Try again
        </Button>
      </div>
    ) : (
      this.props.children
    )
  }
}

export function AnalysisCharts({
  charts,
  controls,
  parameterLabels,
}: {
  charts: AnalysisChart[]
  controls?: (chart: AnalysisChart) => ReactNode
  parameterLabels?: Record<string, string>
}) {
  const [selected, setSelected] = useState('')
  const chart =
    charts.find((item) => item.id === selected) ??
    charts.find((item) => item.status === 'available') ??
    charts[0]
  if (!chart) return null
  const heatmapRows = chart.figure?.data.find((trace) => trace.type === 'heatmap')?.y
  const monthlyHeight =
    chart.id === 'monthly-returns'
      ? Math.min(500, 180 + 40 * Math.max(1, Array.isArray(heatmapRows) ? heatmapRows.length : 1))
      : undefined
  return (
    <section className="space-y-4" aria-label="Analysis charts">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <select
          aria-label="Analysis chart"
          value={chart.id}
          onChange={(event) => setSelected(event.target.value)}
          className="h-10 max-w-full rounded-md border bg-background px-3 text-sm font-medium"
        >
          {charts.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
              {item.status === 'unavailable' ? ' · Needs more evidence' : ''}
            </option>
          ))}
        </select>
        {chart.status === 'available' && (
          <p className="text-xs text-muted-foreground">Hover to inspect · Drag to zoom</p>
        )}
      </div>
      {controls?.(chart)}
      {chart.status === 'available' && chart.figure ? (
        <figure
          className="h-[420px] min-w-0 sm:h-[500px]"
          style={monthlyHeight ? { height: monthlyHeight } : undefined}
          aria-label={chart.title}
        >
          <PlotBoundary key={chart.id}>
            <Suspense
              fallback={
                <output className="block p-8 text-sm text-muted-foreground">Loading chart…</output>
              }
            >
              <ResearchPlot chart={chart} parameterLabels={parameterLabels} />
            </Suspense>
          </PlotBoundary>
        </figure>
      ) : (
        <output className="block rounded-lg border px-5 py-8 text-sm text-muted-foreground">
          {chart.reason ?? 'This chart needs additional recorded evidence.'}
        </output>
      )}
    </section>
  )
}
