import { useEffect, useState } from 'react'
import { type PreparationStatus, researchLibrary } from '@/api/researchLibrary'
import { scannerResearch } from '@/api/scannerResearch'
import { Button } from '@/components/ui/button'

const active = new Set(['queued', 'running', 'cancelling'])

/** Starts acquisition only after the imported setup has reached its saved revision. */
export function ChartinkPreparation({
  experimentId,
  revision,
  enabled,
}: {
  experimentId: string
  revision: number
  enabled: boolean
}) {
  const [status, setStatus] = useState<PreparationStatus | null>(null)
  const [error, setError] = useState(false)
  const [retry, setRetry] = useState(0)
  const [stopped, setStopped] = useState<number | null>(null)

  // biome-ignore lint/correctness/useExhaustiveDependencies: An explicit retry starts a new request for the same saved revision.
  useEffect(() => {
    if (!enabled || stopped === revision) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    setStatus(null)
    setError(false)
    const accept = (value: PreparationStatus) => {
      if (controller.signal.aborted) return
      setStatus(value)
      if (active.has(value.status)) timer = setTimeout(poll, 1500)
    }
    const fail = () => {
      if (!controller.signal.aborted) setError(true)
    }
    const poll = () => {
      researchLibrary.preparation(experimentId, controller.signal).then(accept).catch(fail)
    }
    researchLibrary
      .startPreparation(experimentId, revision, controller.signal)
      .then(accept)
      .catch(fail)
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [experimentId, revision, enabled, stopped, retry])

  const stop = async () => {
    if (!status?.job_id) return
    try {
      await scannerResearch.cancel(status.job_id)
      setStopped(revision)
      setStatus(null)
    } catch {
      setError(true)
    }
  }
  const failed = error || status?.status === 'failed' || status?.status === 'interrupted'
  const paused = stopped === revision || status?.status === 'cancelled'
  const prices = status?.activity?.prices
  const features = status?.activity?.features
  const detail = features?.total_symbols
    ? `${features.completed_symbols ?? 0} of ${features.total_symbols} stocks prepared`
    : prices?.total_symbols
      ? `${prices.checked_symbols ?? 0} of ${prices.total_symbols} stocks checked`
      : 'You can review your settings while this runs.'

  return (
    <section
      aria-label="Background price preparation"
      aria-live="polite"
      className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 px-4 py-3 text-sm"
    >
      <div>
        <p className="font-medium">
          {!enabled
            ? 'Save your settings to refresh prepared data.'
            : failed
              ? 'Background preparation could not finish.'
              : paused
                ? 'Background preparation stopped.'
                : status?.ready
                  ? status.reuse_mode === 'cache'
                    ? 'Price history downloaded'
                    : 'Prices and research inputs ready'
                  : status?.status === 'queued'
                    ? 'Price preparation queued'
                    : 'Preparing prices in the background'}
        </p>
        <p className="text-muted-foreground">
          {status?.ready
            ? status.reuse_mode === 'cache'
              ? 'Run can use the cached prices and finish preparing your research.'
              : 'Run will reuse matching prepared data.'
            : failed || paused
              ? 'Run can still prepare the data normally.'
              : detail}
        </p>
      </div>
      {enabled && (failed || paused) && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setStopped(null)
            setRetry((value) => value + 1)
          }}
        >
          Retry preparation
        </Button>
      )}
      {enabled && status && active.has(status.status) && status.status !== 'cancelling' && (
        <Button variant="ghost" size="sm" onClick={stop}>
          Stop preparation
        </Button>
      )}
    </section>
  )
}
