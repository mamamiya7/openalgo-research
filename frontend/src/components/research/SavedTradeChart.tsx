import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw, Settings2, X } from 'lucide-react'
import { ReplayController, type ReplayState } from 'openalgo-charts'
import type { Widget } from 'openalgo-charts/widget'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SavedTradeChartPage } from '@/api/researchTradeChart'
import { OpenAlgoChart } from '@/components/chart/OpenAlgoChart'
import { REPLAY_SPEEDS } from '@/components/chart/useChartReplay'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  createSavedTradeFeed,
  savedTradeBars,
  savedTradeLookback,
  tradeSeriesMarkers,
  visibleTradeMarkers,
} from './savedTradeChartFeed'
import { guardSavedTradeIndicators, SAVED_TRADE_INDICATORS } from './savedTradeIndicators'

interface Props {
  page: SavedTradeChartPage
  onPage: (offset: number) => void
  pageLoading?: boolean
}

/** Every page/replay mode has its own disposable native workspace. */
export default function SavedTradeChart(props: Props) {
  const { identity, window } = props.page
  return (
    <SavedTradeChartWindow
      key={`${identity.result_artifact}:${identity.period}:${identity.trade_index}:${window.offset}`}
      {...props}
    />
  )
}

function SavedTradeChartWindow({ page, onPage, pageLoading = false }: Props) {
  const [replaying, setReplaying] = useState(false)
  const [widget, setWidget] = useState<Widget | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [frame, setFrame] = useState<ReplayState | null>(null)
  const [speed, setSpeed] = useState(1)
  const speedRef = useRef(speed)
  speedRef.current = speed
  const [error, setError] = useState<string | null>(null)
  const cursor = useRef<number | null>(null)
  const widgetRef = useRef<Widget | null>(null)
  const controller = useRef<ReplayController | null>(null)
  const markerLayer = useRef<ReturnType<Widget['series']['createMarkers']> | null>(null)
  const bars = useMemo(() => savedTradeBars(page), [page])
  const initialIndex = Math.max(
    0,
    Math.min(bars.length - 1, (page.window.entry_index ?? page.window.offset) - page.window.offset)
  )
  const feed = useMemo(() => createSavedTradeFeed(page, () => cursor.current), [page])
  const paged = page.window.total > page.candles.length
  const hasVolume = bars.length > 0 && bars.every((bar) => Number.isFinite(bar.volume))

  const syncFrame = useCallback(
    (next: ReplayState) => {
      cursor.current = next.index
      markerLayer.current?.setMarkers(tradeSeriesMarkers(page, next.index))
      // Native indicator work is deferred until paint. Flush it before reading
      // the new frame, then move legend readouts off any previously hovered bar.
      const chart = widgetRef.current
      if (chart && !chart.isDestroyed) {
        for (const indicator of chart.chart.indicators()) indicator.updateLegendValues(next.index)
      }
      setFrame(next)
    },
    [page]
  )

  const onReady = useCallback((next: Widget | null) => {
    widgetRef.current = next
    setWidget(next)
    setLoaded(false)
  }, [])

  useEffect(() => {
    if (!widget) return
    const restoreGuard = guardSavedTradeIndicators(widget.chart)
    const markers = widget.series.createMarkers()
    markerLayer.current = markers
    markers.setMarkers(tradeSeriesMarkers(page, cursor.current))
    return () => {
      markerLayer.current = null
      try {
        widget.chart.removePrimitive(markers)
      } catch {
        /* Widget already disposed. */
      }
      restoreGuard()
    }
  }, [widget, page])

  useEffect(() => {
    if (!widget || !loaded || !replaying || controller.current) return
    // Native ReplayController truncates the source before indicator recalculation.
    // The private feed's cursor also prevents a refresh from returning future bars.
    const replay = new ReplayController(widget.chart, {
      series: widget.series,
      bars,
      startIndex: initialIndex,
      speed: speedRef.current,
      onFrame: syncFrame,
    })
    controller.current = replay
    syncFrame(replay.state())
    return () => {
      try {
        replay.pause()
        replay.stop()
      } catch {
        /* The child widget may already have disposed its series. */
      }
      controller.current = null
    }
  }, [widget, loaded, replaying, bars, initialIndex, syncFrame])

  // A hidden tab must never keep consuming the replay in the background.
  useEffect(() => {
    const pause = () => {
      if (!document.hidden || !controller.current) return
      controller.current.pause()
      syncFrame(controller.current.state())
    }
    document.addEventListener('visibilitychange', pause)
    return () => document.removeEventListener('visibilitychange', pause)
  }, [syncFrame])

  const changeReplay = (active: boolean) => {
    controller.current?.pause()
    // Rebuilding rather than restoring drawings prevents full-result annotations
    // from revealing future exits. No chart layout is persisted to localStorage.
    cursor.current = active ? initialIndex : null
    setFrame(null)
    setLoaded(false)
    setError(null)
    setReplaying(active)
  }
  const move = (action: (replay: ReplayController) => void) => {
    const replay = controller.current
    if (!replay) return
    action(replay)
    syncFrame(replay.state())
  }
  const details = visibleTradeMarkers(page, replaying ? (frame?.index ?? initialIndex) : null)
  const date = (time: number) =>
    new Intl.DateTimeFormat('en-IN', {
      timeZone: page.interval === 'D' ? 'UTC' : page.timezone,
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      ...(page.interval === '1m' ? ({ hour: '2-digit', minute: '2-digit' } as const) : {}),
    }).format(time * 1000)
  const fillTime = (timestamp: string) => {
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(timestamp)
    const qualified = dateOnly
      ? `${timestamp}T00:00:00Z`
      : /(?:Z|[+-]\d{2}:?\d{2})$/i.test(timestamp)
        ? timestamp
        : `${timestamp}+05:30`
    const value = Date.parse(qualified)
    if (!Number.isFinite(value)) return timestamp
    return new Intl.DateTimeFormat('en-IN', {
      timeZone: dateOnly ? 'UTC' : page.timezone,
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      ...(!dateOnly
        ? ({ hour: '2-digit', minute: '2-digit', timeZoneName: 'short' } as const)
        : {}),
    }).format(value)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
        <span className="mr-auto text-muted-foreground">
          {page.interval === 'D' ? 'Daily candles' : '1-minute candles'} · {page.exchange}
          {replaying && ' · Replay'}
        </span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" disabled={!loaded || pageLoading}>
              Indicators
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="motion-reduce:animate-none">
            {SAVED_TRADE_INDICATORS.filter(
              (indicator) => hasVolume || !['volume', 'vwap'].includes(indicator.id)
            ).map((indicator) => (
              <DropdownMenuItem
                key={indicator.id}
                onSelect={() => {
                  try {
                    widget?.chart.addIndicator(indicator.id)
                    setError(null)
                  } catch {
                    setError('This indicator is customized. Choose another built-in study.')
                  }
                }}
              >
                {indicator.name}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8"
          title="Chart settings"
          aria-label="Chart settings"
          disabled={!loaded}
          onClick={() => widget?.openSettings()}
        >
          <Settings2 className="size-4" />
        </Button>
        <Button
          variant={replaying ? 'secondary' : 'outline'}
          size="sm"
          disabled={(!loaded && !replaying) || pageLoading || bars.length < 2}
          onClick={() => changeReplay(!replaying)}
        >
          <RotateCcw className="mr-1 size-3.5" />
          {replaying ? 'Exit replay' : paged ? 'Replay section' : 'Replay'}
        </Button>
      </div>

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border">
        <OpenAlgoChart
          key={replaying ? 'replay' : 'review'}
          feed={feed}
          dataEndsAt={bars.at(-1)?.time}
          symbol={page.symbol}
          exchange={page.exchange}
          interval={page.interval}
          intervals={[page.interval]}
          lookbackBars={savedTradeLookback(page)}
          topbar={false}
          rail={!replaying}
          // The widget status line caches a hovered OHLC bar independently of
          // replay. Keep it out of replay so stepping back cannot retain a future
          // price, timestamp, change or full-series count in its readout.
          statusline={!replaying}
          indicators={false}
          customIndicators={false}
          onReady={onReady}
          onData={(event) => {
            if (event.error) {
              setError('Saved prices could not be displayed. Reopen the chart.')
              return
            }
            if (event.bars > 0) {
              if (!loaded && widget)
                widget.chart.setVisibleLogicalRange({ from: -1, to: event.bars + 1 })
              setLoaded(true)
            }
          }}
        />
        {replaying && (
          <div className="flex shrink-0 flex-wrap items-center gap-1 border-t bg-muted/30 p-2 text-xs">
            <span className="mr-1 font-medium">{paged ? 'Section replay' : 'Bar replay'}</span>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="Previous bar"
              disabled={!frame || frame.index === 0}
              onClick={() => move((r) => r.stepBack())}
            >
              <ChevronLeft className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label={frame?.playing ? 'Pause' : 'Play'}
              disabled={!frame}
              onClick={() => move((r) => (frame?.playing ? r.pause() : r.play({ speed })))}
            >
              {frame?.playing ? <Pause className="size-4" /> : <Play className="size-4" />}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="Next bar"
              disabled={!frame || frame.index >= bars.length - 1}
              onClick={() => move((r) => r.step())}
            >
              <ChevronRight className="size-4" />
            </Button>
            <input
              type="range"
              min={0}
              max={Math.max(0, bars.length - 1)}
              value={frame?.index ?? initialIndex}
              onChange={(event) => move((r) => r.seek(Number(event.target.value)))}
              aria-label="Replay position"
              className="mx-2 h-1 min-w-20 flex-1 accent-primary"
            />
            <select
              aria-label="Replay speed"
              value={speed}
              className="h-7 rounded border bg-background px-1"
              onChange={(event) => {
                const value = Number(event.target.value)
                setSpeed(value)
                if (frame?.playing) move((r) => r.play({ speed: value }))
              }}
            >
              {REPLAY_SPEEDS.map((value) => (
                <option key={value} value={value}>
                  {value}×
                </option>
              ))}
            </select>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="Exit replay"
              onClick={() => changeReplay(false)}
            >
              <X className="size-4" />
            </Button>
            <span className="w-full text-muted-foreground tabular-nums sm:w-auto">
              {frame?.bar ? date(frame.bar.time) : 'Preparing replay…'}
            </span>
          </div>
        )}
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {details.map((marker) => (
          <span key={marker.kind} title={`${marker.timestamp} · ${marker.basis}`}>
            <span className="font-medium text-foreground">
              {marker.kind === 'entry' ? 'Entry' : 'Exit'}
            </span>{' '}
            {marker.price.toLocaleString('en-IN', { maximumFractionDigits: 4 })} ·{' '}
            {fillTime(marker.timestamp)}
          </span>
        ))}
        {replaying &&
          details.length === 0 &&
          page.window.entry_index !== null &&
          page.window.offset + (frame?.index ?? initialIndex) < page.window.entry_index && (
            <span>Before entry</span>
          )}
      </div>
      {page.notice && !replaying && (
        <p className="shrink-0 text-xs text-muted-foreground">{page.notice}</p>
      )}
      {paged && (
        <div className="flex shrink-0 items-center justify-between gap-2 text-xs text-muted-foreground">
          <Button
            variant="ghost"
            size="sm"
            disabled={pageLoading || page.window.previous_offset === null}
            onClick={() =>
              page.window.previous_offset !== null && onPage(page.window.previous_offset)
            }
          >
            <ChevronLeft className="mr-1 size-4" />
            Earlier
          </Button>
          <span className="text-center tabular-nums">
            Candles {(page.window.offset + 1).toLocaleString()}–
            {(page.window.offset + bars.length).toLocaleString()} of{' '}
            {page.window.total.toLocaleString()}
          </span>
          <Button
            variant="ghost"
            size="sm"
            disabled={pageLoading || page.window.next_offset === null}
            onClick={() => page.window.next_offset !== null && onPage(page.window.next_offset)}
          >
            Later
            <ChevronRight className="ml-1 size-4" />
          </Button>
        </div>
      )}
    </div>
  )
}
