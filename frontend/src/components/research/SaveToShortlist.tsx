import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bookmark, Check } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import {
  researchShortlist,
  type SaveCandidate,
  type ShortlistPage,
  shortlistError,
  shortlistKey,
} from '@/api/researchShortlist'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/authStore'

interface Props {
  experimentId: string
  jobId: string
  configId?: string
  proposalNumber?: number
  readOnly?: boolean
}
export function SaveToShortlist(props: Props) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  return (
    <SaveCandidateButton
      key={JSON.stringify([
        owner,
        props.experimentId,
        props.jobId,
        props.configId,
        props.proposalNumber,
      ])}
      {...props}
      owner={owner}
    />
  )
}
function SaveCandidateButton({
  experimentId,
  jobId,
  configId,
  proposalNumber,
  readOnly = false,
  owner,
}: Props & { owner: string }) {
  const client = useQueryClient()
  const prefix = shortlistKey(owner, experimentId)
  const key = [...prefix, 'saved', jobId, configId, readOnly]
  const saved = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      researchShortlist.list(
        experimentId,
        { job_id: jobId, ...(configId ? { config_id: configId } : {}) },
        signal
      ),
    retry: false,
    staleTime: 0,
    gcTime: 0,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  async function save() {
    if (request.current || readOnly) return
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError(null)
    const body: SaveCandidate = {
      job_id: jobId,
      ...(configId ? { config_id: configId } : {}),
      ...(proposalNumber != null ? { proposal_number: proposalNumber } : {}),
    }
    try {
      const result = await researchShortlist.save(experimentId, body, controller.signal)
      if (controller.signal.aborted) return
      client.setQueryData<ShortlistPage>(key, {
        version: 'research-shortlist-v1',
        experiment_id: experimentId,
        archived: result.candidate.archived,
        items: [result.candidate],
        total: 1,
        next_offset: null,
      })
      await client.invalidateQueries({ queryKey: prefix })
    } catch (cause) {
      if (!controller.signal.aborted) setError(shortlistError(cause))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
      if (request.current === controller) request.current = null
    }
  }
  const isSaved = Boolean(saved.data?.items.length)
  return (
    <div className="space-y-1">
      <Button
        type="button"
        variant="outline"
        disabled={isSaved || busy || readOnly || saved.isPending}
        onClick={() => void save()}
      >
        {isSaved ? <Check className="mr-2 size-4" /> : <Bookmark className="mr-2 size-4" />}
        {isSaved ? 'Shortlisted' : busy ? 'Saving…' : 'Shortlist'}
      </Button>
      {error && (
        <p role="alert" className="max-w-xs text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
