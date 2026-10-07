import { lazy, Suspense, useEffect, useState } from 'react'
import {
  researchTradeChart,
  type SavedTradeChartPage,
  type SavedTradeChartRequest,
  tradeChartError,
} from '@/api/researchTradeChart'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

const SavedTradeChart = lazy(() => import('./SavedTradeChart'))

interface Props {
  request: SavedTradeChartRequest
  symbol: string
  strategy: string
  opener: HTMLElement | null
  onClose: () => void
}

function LoadingChart() {
  return (
    <output className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
      Opening saved prices…
    </output>
  )
}

/** Mounted only on an explicit trade action. No account-wide candle cache. */
export function SavedTradeChartDialog({ request, symbol, strategy, opener, onClose }: Props) {
  const [offset, setOffset] = useState(0)
  const [attempt, setAttempt] = useState(0)
  const [page, setPage] = useState<SavedTradeChartPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const { jobId, resultArtifact, period, tradeIndex } = request

  // biome-ignore lint/correctness/useExhaustiveDependencies: An explicit retry re-reads the same immutable page.
  useEffect(() => {
    const controller = new AbortController()
    setPage(null)
    setLoading(true)
    setError(null)
    void researchTradeChart
      .get({ jobId, resultArtifact, period, tradeIndex }, offset, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return
        if (
          next.identity.job_id !== jobId ||
          next.identity.result_artifact !== resultArtifact ||
          next.identity.period !== period ||
          next.identity.trade_index !== tradeIndex ||
          next.window.offset !== offset
        ) {
          throw new Error('Saved chart identity changed')
        }
        setPage(next)
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(tradeChartError(failure))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [jobId, resultArtifact, period, tradeIndex, offset, attempt])

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="flex h-[min(880px,94dvh)] max-w-[calc(100%-1rem)] flex-col gap-3 overflow-hidden p-3 motion-reduce:animate-none sm:max-w-[min(1440px,96vw)] sm:p-5"
        onCloseAutoFocus={(event) => {
          if (opener?.isConnected) {
            event.preventDefault()
            opener.focus()
          }
        }}
      >
        <DialogHeader className="shrink-0 pr-8 text-left">
          <DialogTitle>{symbol} · Trade chart</DialogTitle>
          <DialogDescription>{strategy} · Saved prices</DialogDescription>
        </DialogHeader>
        {error ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center">
            <p className="text-sm text-muted-foreground" role="alert">
              {error}
            </p>
            <Button variant="outline" size="sm" onClick={() => setAttempt((value) => value + 1)}>
              Try again
            </Button>
          </div>
        ) : loading || !page ? (
          <LoadingChart />
        ) : (
          <Suspense fallback={<LoadingChart />}>
            <SavedTradeChart page={page} onPage={setOffset} pageLoading={loading} />
          </Suspense>
        )}
      </DialogContent>
    </Dialog>
  )
}
