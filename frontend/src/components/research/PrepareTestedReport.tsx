import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { researchCandidates } from '@/api/researchCandidates'
import { Button } from '@/components/ui/button'
import { researchError } from '@/hooks/useResearchExperiment'
import { useAuthStore } from '@/stores/authStore'

/** Materialise a recorded candidate once; never starts another optimisation. */
export function PrepareTestedReport({
  jobId,
  configId,
  readOnly,
  onOpen,
}: {
  jobId: string
  configId: string
  readOnly: boolean
  onOpen: (jobId: string) => void
}) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const client = useQueryClient()
  const key = ['research-candidates', owner, jobId]
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const active = useRef<AbortController | null>(null)
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => researchCandidates.get(jobId, signal),
    retry: false,
    staleTime: 15000,
    refetchInterval: (query) =>
      query.state.data?.candidates.some((item) => ['queued', 'running'].includes(item.status))
        ? 1500
        : false,
  })
  useEffect(() => {
    // A late response must not update another account or candidate's screen.
    void owner
    void jobId
    void configId
    setBusy(false)
    setError(null)
    return () => {
      active.current?.abort()
      active.current = null
    }
  }, [owner, jobId, configId])
  useEffect(() => {
    if (readOnly) {
      active.current?.abort()
      active.current = null
      setBusy(false)
    }
  }, [readOnly])
  const receipt = query.data?.candidates.find((item) => item.config_id === configId)
  async function prepare() {
    if (readOnly || active.current || receipt?.status !== 'available') return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      const next = await researchCandidates.prepare(jobId, configId, controller.signal)
      if (controller.signal.aborted) return
      client.setQueryData(key, (previous: typeof query.data) =>
        previous
          ? {
              ...previous,
              candidates: previous.candidates.map((item) =>
                item.config_id === configId ? next : item
              ),
            }
          : previous
      )
      await client.invalidateQueries({ queryKey: key })
    } catch (cause) {
      if (!controller.signal.aborted) setError(researchError(cause))
    } finally {
      if (active.current === controller) {
        active.current = null
        setBusy(false)
      }
    }
  }
  const preparing = busy || receipt?.status === 'queued' || receipt?.status === 'running'
  return (
    <div className="max-w-md space-y-2">
      {receipt?.status === 'ready' && receipt.report_job_id ? (
        <Button onClick={() => onOpen(receipt.report_job_id!)}>Open tested report</Button>
      ) : receipt?.status === 'failed' || receipt?.status === 'mismatch' ? (
        <Button
          variant="outline"
          disabled={!receipt.report_job_id}
          onClick={() => receipt.report_job_id && onOpen(receipt.report_job_id)}
        >
          View stopped report
        </Button>
      ) : (
        <Button
          disabled={readOnly || preparing || receipt?.status !== 'available'}
          onClick={() => void prepare()}
        >
          {preparing
            ? 'Preparing tested report…'
            : query.isPending
              ? 'Checking saved report…'
              : 'Prepare tested report'}
        </Button>
      )}
      <p className="text-xs text-muted-foreground" role={preparing ? 'status' : undefined}>
        {preparing
          ? 'Using recorded settings and prices. Your study is already complete.'
          : receipt?.status === 'ready'
            ? 'Inspect the report, then save a reusable setup.'
            : (receipt?.error ??
              (readOnly
                ? 'This study is read-only.'
                : 'One exact calculation saves the trades for reuse. No price download or new search.'))}
      </p>
      {(error || query.isError) && (
        <p role="alert" className="text-xs text-destructive">
          {error ?? researchError(query.error)}
          <Button size="sm" variant="link" onClick={() => void query.refetch()}>
            Check again
          </Button>
        </p>
      )}
    </div>
  )
}
