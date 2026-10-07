import { isAxiosError } from 'axios'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { PortfolioJob } from '@/api/portfolioResearch'
import { type ResearchExperiment, researchLibrary } from '@/api/researchLibrary'
import type { PortfolioDraft } from '@/components/research/PortfolioBuilder'
import { useAuthStore } from '@/stores/authStore'

export function researchError(error: unknown): string {
  return isAxiosError(error) && typeof error.response?.data?.message === 'string'
    ? error.response.data.message
    : error instanceof Error
      ? error.message
      : 'Unable to save. Try again.'
}
type SaveState = 'saved' | 'unsaved' | 'saving' | 'conflict' | 'error'
const serialized = (draft: PortfolioDraft) => JSON.stringify(draft)
const savedTime = (value: number | string | undefined) => {
  if (value == null) return 0
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : Date.parse(String(value)) / 1000 || 0
}
export function useResearchExperiment(initial: ResearchExperiment, owner: string) {
  const key = `research-edit:${owner}:${initial.id}`
  const [server, setServer] = useState(initial)
  const serverRef = useRef(initial)
  const [draft, setDraft] = useState(initial.draft)
  const draftRef = useRef(initial.draft)
  const [state, setState] = useState<SaveState>('saved')
  const stateRef = useRef<SaveState>('saved')
  const [error, setError] = useState<string | null>(null)
  const dirty = useRef(false)
  const generation = useRef(0)
  const inFlight = useRef<Promise<ResearchExperiment> | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const jobsRequest = useRef<AbortController | null>(null)
  const metadataRequest = useRef<AbortController | null>(null)
  const mounted = useRef(true)
  const ownerMatches = useCallback(
    () => (useAuthStore.getState().user?.username ?? 'account') === owner,
    [owner]
  )
  const saveState = useCallback((next: SaveState, text: string | null = null) => {
    stateRef.current = next
    if (mounted.current) {
      setState(next)
      setError(text)
    }
  }, [])
  const buffer = useCallback(() => {
    try {
      if (!ownerMatches()) {
        sessionStorage.removeItem(key)
        return
      }
      if (dirty.current)
        sessionStorage.setItem(
          key,
          JSON.stringify({
            revision: serverRef.current.revision,
            draft: draftRef.current,
          })
        )
      else sessionStorage.removeItem(key)
    } catch {
      /* A failed server save remains visible even without browser recovery storage. */
    }
  }, [key, ownerMatches])
  const accept = useCallback(
    (next: ResearchExperiment) => {
      serverRef.current = next
      draftRef.current = next.draft
      dirty.current = false
      generation.current += 1
      if (mounted.current) {
        setServer(next)
        setDraft(next.draft)
      }
      saveState('saved')
      buffer()
    },
    [buffer, saveState]
  )
  const flush = useCallback(
    async function flushPending(): Promise<ResearchExperiment> {
      if (timer.current) {
        clearTimeout(timer.current)
        timer.current = null
      }
      if (inFlight.current) {
        await inFlight.current
        return dirty.current ? flushPending() : serverRef.current
      }
      if (!dirty.current) return serverRef.current
      if (!ownerMatches()) throw new Error('Sign in to the account that owns this experiment.')
      if (stateRef.current === 'conflict')
        throw new Error('Resolve the saved draft conflict before continuing.')
      const sent = generation.current
      const payload = draftRef.current
      saveState('saving')
      const pending = researchLibrary
        .saveDraft(initial.id, serverRef.current.revision, payload)
        .then((next) => {
          serverRef.current = next
          if (mounted.current) setServer(next)
          if (generation.current === sent) {
            dirty.current = false
            draftRef.current = next.draft
            if (mounted.current) setDraft(next.draft)
          }
          buffer()
          saveState(dirty.current ? 'unsaved' : 'saved')
          return next
        })
        .catch((cause: unknown) => {
          buffer()
          saveState(
            isAxiosError(cause) && cause.response?.status === 409 ? 'conflict' : 'error',
            researchError(cause)
          )
          throw cause
        })
        .finally(() => {
          inFlight.current = null
        })
      inFlight.current = pending
      await pending
      return dirty.current ? flushPending() : serverRef.current
    },
    [buffer, initial.id, ownerMatches, saveState]
  )
  const change = useCallback(
    (next: PortfolioDraft) => {
      draftRef.current = next
      generation.current += 1
      dirty.current = serialized(next) !== serialized(serverRef.current.draft)
      setDraft(next)
      buffer()
      if (stateRef.current === 'conflict') return
      saveState(dirty.current ? 'unsaved' : 'saved')
      if (timer.current) clearTimeout(timer.current)
      if (dirty.current)
        timer.current = setTimeout(() => {
          void flush().catch(() => {})
        }, 600)
    },
    [buffer, flush, saveState]
  )
  useEffect(() => {
    mounted.current = true
    try {
      const local = JSON.parse(sessionStorage.getItem(key) ?? 'null')
      if (
        local?.draft?.portfolio?.version === 'research-portfolio-v1' &&
        Array.isArray(local.draft.portfolio.strategies) &&
        local.draft.portfolio.strategies.length <= 8 &&
        local.draft.portfolio.strategies.every(
          (item: Record<string, unknown>) =>
            item &&
            typeof item.id === 'string' &&
            typeof item.source_id === 'string' &&
            item.config &&
            item.search
        ) &&
        typeof local.draft.optimizing === 'boolean' &&
        local.draft.optimization &&
        local.draft.sources
      ) {
        if (serialized(local.draft) !== serialized(initial.draft)) {
          draftRef.current = local.draft
          dirty.current = true
          generation.current += 1
          setDraft(local.draft)
          if (local.revision !== initial.revision)
            saveState('conflict', 'A different draft was saved while you were away.')
          else {
            saveState('unsaved')
            timer.current = setTimeout(() => {
              void flush().catch(() => {})
            }, 600)
          }
        } else sessionStorage.removeItem(key)
      }
    } catch {
      /* Malformed local recovery data never replaces a saved server draft. */
    }
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty.current || inFlight.current) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => {
      mounted.current = false
      jobsRequest.current?.abort()
      metadataRequest.current?.abort()
      window.removeEventListener('beforeunload', beforeUnload)
      if (timer.current) clearTimeout(timer.current)
      if (ownerMatches()) void flush().catch(() => {})
      else {
        try {
          sessionStorage.removeItem(key)
        } catch {
          /* Browser storage unavailable. */
        }
      }
    }
  }, [flush, initial.draft, initial.revision, key, ownerMatches, saveState])
  const reload = useCallback(async () => {
    const next = await researchLibrary.get(initial.id)
    accept(next)
  }, [accept, initial.id])
  const refreshMetadata = useCallback(async () => {
    const before = await flush()
    const sent = generation.current
    metadataRequest.current?.abort()
    const controller = new AbortController()
    metadataRequest.current = controller
    try {
      const next = await researchLibrary.get(initial.id, controller.signal)
      if (controller.signal.aborted || !mounted.current || !ownerMatches()) return
      if (
        generation.current !== sent ||
        serverRef.current.revision !== before.revision ||
        serialized(next.draft) !== serialized(before.draft)
      ) {
        dirty.current = true
        buffer()
        const message = 'A different draft was saved. Review the draft conflict before continuing.'
        saveState('conflict', message)
        throw new Error(message)
      }
      accept(next)
    } finally {
      if (metadataRequest.current === controller) metadataRequest.current = null
    }
  }, [accept, buffer, flush, initial.id, ownerMatches, saveState])
  const reject = useCallback(
    (cause: unknown) => {
      if (isAxiosError(cause) && cause.response?.status === 409) {
        dirty.current = true
        buffer()
        saveState('conflict', researchError(cause))
      }
    },
    [buffer, saveState]
  )
  const reflectJob = useCallback((job: PortfolioJob) => {
    const { result: _result, ...receipt } = job
    const before = serverRef.current
    const jobs = before.jobs.map((item) => (item.id === job.id ? { ...item, ...receipt } : item))
    if (JSON.stringify(jobs) === JSON.stringify(before.jobs)) return
    serverRef.current = { ...before, jobs }
    if (mounted.current) setServer(serverRef.current)
  }, [])
  const refreshJobs = useCallback(async () => {
    if (!mounted.current || !ownerMatches()) return
    jobsRequest.current?.abort()
    const controller = new AbortController()
    jobsRequest.current = controller
    try {
      const next = await researchLibrary.get(initial.id, controller.signal)
      if (controller.signal.aborted || !mounted.current || !ownerMatches()) return
      const before = serverRef.current
      // A new candidate changes library membership, not the editable setup.
      // Preserve the draft, revision/conflict and any newer polled job receipt.
      const known = new Map(before.jobs.map((job) => [job.id, job]))
      for (const job of next.jobs) {
        const previous = known.get(job.id)
        if (
          !previous ||
          savedTime(job.updated_at ?? job.created_at) >=
            savedTime(previous.updated_at ?? previous.created_at)
        )
          known.set(job.id, job)
      }
      const jobs = [...known.values()]
        .sort(
          (left, right) =>
            savedTime(right.created_at) - savedTime(left.created_at) ||
            left.id.localeCompare(right.id)
        )
        .slice(0, Math.max(20, before.jobs.length, next.jobs.length))
      serverRef.current = {
        ...before,
        jobs,
        job_count: next.job_count,
        active_job_count: next.active_job_count,
        jobs_next_offset: next.jobs_next_offset,
      }
      setServer(serverRef.current)
    } catch (cause) {
      if (!controller.signal.aborted && mounted.current && ownerMatches()) throw cause
    } finally {
      if (jobsRequest.current === controller) jobsRequest.current = null
    }
  }, [initial.id, ownerMatches])
  return {
    server,
    draft,
    state,
    error,
    change,
    flush,
    accept,
    reload,
    reject,
    reflectJob,
    refreshJobs,
    refreshMetadata,
  }
}
