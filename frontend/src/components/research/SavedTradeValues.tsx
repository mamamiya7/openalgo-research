import type { Chart, ReplayState } from 'openalgo-charts'
import { applyTokens, widgetTokens } from 'openalgo-charts/widget'
import { useEffect, useRef } from 'react'
import { mountChartDataWindow } from '@/components/chart/mountChartDataWindow'
import { buildChartTheme, isLightTheme } from '@/lib/trading/chartTheme'
import { useThemeStore } from '@/stores/themeStore'

/** Native readings cache hovered time. A refresh alone can retain a later bar. */
export function resetSavedTradeReadout(chart: Chart, frame: ReplayState) {
  chart.emit('crosshair:readout', {
    index: frame.index,
    point: null,
    time: frame.bar?.time ?? null,
  })
}

export function SavedTradeValues({ chart, id }: { chart: Chart; id: string }) {
  const root = useRef<HTMLElement>(null)
  const host = useRef<HTMLDivElement>(null)
  const { mode, appMode } = useThemeStore()
  useEffect(() => {
    if (!host.current || chart.isDestroyed) return
    const readings = mountChartDataWindow(chart, host.current)
    return () => readings.destroy()
  }, [chart])
  useEffect(() => {
    if (!root.current) return
    const light = isLightTheme(mode, appMode)
    root.current.dataset.theme = light ? 'light' : 'dark'
    applyTokens(
      root.current,
      widgetTokens(buildChartTheme(mode, appMode), light ? 'light' : 'dark')
    )
  }, [mode, appMode])
  return (
    <aside
      ref={root}
      id={id}
      aria-label="Saved chart values"
      className="oac-widget max-h-[32dvh] shrink-0 overflow-y-auto border-t md:max-h-none md:w-64 md:border-l md:border-t-0"
      style={{ display: 'block', height: 'auto', background: 'transparent', overflowY: 'auto' }}
    >
      <h3 className="px-3 pt-3 text-xs font-medium">Values</h3>
      <div ref={host} />
    </aside>
  )
}
