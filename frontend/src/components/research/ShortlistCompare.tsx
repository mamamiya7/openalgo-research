import { useQueries, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { comparisonError, comparisonKey, researchComparisons } from '@/api/researchComparisons'
import { researchShortlist, shortlistKey } from '@/api/researchShortlist'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'

export function comparisonSelection(params: URLSearchParams): string[] {
  return [
    ...new Set(
      (params.get('compare_candidates') ?? '').split(',').filter((id) => /^[\w-]{1,128}$/.test(id))
    ),
  ].slice(0, 4)
}

export function ShortlistCompare({
  experimentId,
  owner,
  readOnly,
  onOpen,
}: {
  experimentId: string
  owner: string
  readOnly: boolean
  onOpen: (id: string) => void
}) {
  const [params, setParams] = useSearchParams()
  const ids = comparisonSelection(params)
  const reference = params.get('compare_reference') ?? ''
  const token = params.get('compare_request')
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const active = useRef<AbortController | null>(null)
  useEffect(() => () => active.current?.abort(), [])
  useEffect(() => {
    if (readOnly) {
      active.current?.abort()
      active.current = null
      setBusy(false)
    }
  }, [readOnly])
  const details = useQueries({
    queries: ids.map((id) => ({
      queryKey: [...shortlistKey(owner, experimentId), 'compare', id, readOnly],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        researchShortlist.get(experimentId, id, signal),
      retry: false,
      gcTime: 0,
      staleTime: 0,
    })),
  })
  const ready = details.every(
    (item) =>
      item.data?.available && item.data.candidate.report.status === 'ready' && !item.data.archived
  )
  function changeReference(value: string) {
    const next = new URLSearchParams(params)
    next.set('compare_reference', value)
    next.delete('compare_request')
    setError(null)
    setParams(next, { replace: true })
  }
  async function save() {
    if (
      active.current ||
      readOnly ||
      ids.length < 2 ||
      !ids.includes(reference) ||
      (!ready && !token)
    )
      return
    const request = token || crypto.randomUUID()
    const next = new URLSearchParams(params)
    next.set('compare_request', request)
    setParams(next, { replace: true })
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      const saved = await researchComparisons.save(
        experimentId,
        { request_id: request, candidate_ids: ids, reference_candidate_id: reference },
        controller.signal
      )
      if (controller.signal.aborted) return
      void client.invalidateQueries({ queryKey: comparisonKey(owner, experimentId) })
      onOpen(saved.comparison.id)
    } catch (cause) {
      if (!controller.signal.aborted) setError(comparisonError(cause))
    } finally {
      if (!controller.signal.aborted) {
        active.current = null
        setBusy(false)
      }
    }
  }
  if (!ids.length)
    return <p className="text-xs text-muted-foreground">Select 2–4 saved reports to compare.</p>
  return (
    <section className="space-y-3 rounded-lg border p-4" aria-label="Compare selected candidates">
      <div className="flex flex-wrap items-end gap-4">
        <p className="self-center text-sm">{ids.length} selected</p>
        <div className="min-w-0 flex-1 space-y-1.5">
          <Label htmlFor="comparison-reference">Reference</Label>
          <select
            id="comparison-reference"
            value={reference}
            disabled={busy || readOnly}
            onChange={(event) => changeReference(event.target.value)}
            className="h-9 w-full min-w-0 rounded-md border bg-background px-3 text-sm"
          >
            <option value="">Choose a reference</option>
            {ids.map((id, index) => (
              <option key={id} value={id}>
                {details[index].data?.candidate.name ?? `Saved candidate ${index + 1}`}
              </option>
            ))}
          </select>
        </div>
        <Button
          disabled={
            busy || readOnly || ids.length < 2 || !ids.includes(reference) || (!ready && !token)
          }
          onClick={() => void save()}
        >
          {busy ? 'Saving comparison…' : 'Compare'}
        </Button>
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() => {
            const next = new URLSearchParams(params)
            for (const key of ['compare_candidates', 'compare_reference', 'compare_request'])
              next.delete(key)
            setParams(next, { replace: true })
          }}
        >
          Clear
        </Button>
      </div>
      {!ready && details.every((item) => !item.isPending) && !token && (
        <p className="text-xs text-muted-foreground">
          Open the candidate and prepare its report before comparing.
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}
