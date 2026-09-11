import { useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { ArrowLeft, FileSpreadsheet } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  type ChartinkImportPayload,
  chartinkPayload,
  chartinkRequestId,
  researchChartink,
} from '@/api/researchChartink'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/authStore'

const currentOwner = () => useAuthStore.getState().user?.username ?? 'account'
export function ChartinkImport({ requestId, owner }: { requestId: string; owner: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<'waiting' | 'saving' | 'error'>('waiting')
  const [error, setError] = useState<string | null>(null)
  const payload = useRef<ChartinkImportPayload | null>(null)
  const priorOwner = useRef(owner)
  const priorRequest = useRef(requestId)
  // biome-ignore lint/correctness/useExhaustiveDependencies: An explicit retry restarts the handshake or retries the retained payload.
  useEffect(() => {
    if (priorOwner.current !== owner) {
      priorOwner.current = owner
      payload.current = null
      setState('error')
      setError('Your account changed. Try again to import into this account.')
      return
    }
    if (priorRequest.current !== requestId) {
      priorRequest.current = requestId
      payload.current = null
    }
    if (!chartinkRequestId(requestId)) {
      setState('error')
      setError(
        'This import link is incomplete. Open the Chartink extension and send the history again.'
      )
      return
    }
    let alive = true
    let consumed = false
    let resend: number | undefined
    let expiry: number | undefined
    const controller = new AbortController()
    const origin = window.location.origin
    const send = (message: Record<string, unknown>) =>
      window.postMessage({ ...message, version: 1, requestId }, origin)
    const clearWait = () => {
      window.clearInterval(resend)
      window.clearTimeout(expiry)
    }
    const fail = (message: string) => {
      if (!alive || currentOwner() !== owner) return
      clearWait()
      setState('error')
      setError(message)
      send({ type: 'openalgo:chartink-result', ok: false, message })
    }
    const submit = async (input: ChartinkImportPayload) => {
      if (consumed || !alive || currentOwner() !== owner) return
      consumed = true
      payload.current = input
      clearWait()
      setState('saving')
      setError(null)
      try {
        const result = await researchChartink.importHistory(input, controller.signal)
        if (!alive || controller.signal.aborted || currentOwner() !== owner) return
        if (
          result.protocol_version !== 1 ||
          typeof result.experiment_id !== 'string' ||
          !/^[a-f0-9]{32}$/.test(result.experiment_id)
        )
          throw new Error('Unexpected import receipt')
        send({ type: 'openalgo:chartink-result', ok: true, experimentId: result.experiment_id })
        void queryClient.invalidateQueries({ queryKey: ['research-library', owner] })
        navigate(
          `/scanner-research?experiment=${encodeURIComponent(result.experiment_id)}&view=setup`,
          { replace: true }
        )
      } catch (cause) {
        if (controller.signal.aborted) return
        const message =
          isAxiosError(cause) && typeof cause.response?.data?.message === 'string'
            ? cause.response.data.message.slice(0, 300)
            : 'Could not save this history. Try again; the same import will be reused.'
        fail(message)
      }
    }
    const receive = (event: MessageEvent) => {
      if (!alive || consumed || event.source !== window || event.origin !== origin) return
      const message = event.data
      if (
        message?.type === 'openalgo:chartink-missing' &&
        message.version === 1 &&
        message.requestId === requestId
      ) {
        consumed = true
        fail('Return to your Chartink scanner and reopen the extension, then try again.')
        return
      }
      if (
        message?.type === 'openalgo:chartink-bridge-ready' &&
        message.version === 1 &&
        message.requestId === requestId
      ) {
        send({ type: 'openalgo:chartink-ready' })
        return
      }
      if (
        !message ||
        message.type !== 'openalgo:chartink-import' ||
        message.version !== 1 ||
        message.requestId !== requestId
      )
        return
      const input = chartinkPayload(message.payload, requestId)
      if (!input) {
        consumed = true
        fail(
          'This is not a supported Chartink history export. Open the extension and send the history again.'
        )
        return
      }
      void submit(input)
    }
    window.addEventListener('message', receive)
    if (payload.current) void submit(payload.current)
    else {
      setState('waiting')
      setError(null)
      const ready = () => send({ type: 'openalgo:chartink-ready' })
      resend = window.setInterval(ready, 1000)
      expiry = window.setTimeout(() => {
        consumed = true
        fail(
          'No history arrived. Return to your Chartink scanner and reopen the extension, then try again.'
        )
      }, 15000)
      ready()
    }
    return () => {
      alive = false
      clearWait()
      controller.abort()
      window.removeEventListener('message', receive)
    }
  }, [requestId, owner, attempt, navigate, queryClient])
  return (
    <main className="mx-auto w-full max-w-2xl px-4 py-8 sm:px-6">
      <Button variant="ghost" size="sm" onClick={() => navigate('/scanner-research')}>
        <ArrowLeft className="mr-2 size-4" aria-hidden="true" />
        Research library
      </Button>
      <section className="mt-12 space-y-5" aria-labelledby="chartink-import-heading">
        <span
          className={`flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary ${state !== 'error' ? 'motion-safe:animate-pulse' : ''}`}
        >
          <FileSpreadsheet className="size-6" aria-hidden="true" />
        </span>
        <div className="space-y-2">
          <h1 id="chartink-import-heading" className="text-2xl font-semibold tracking-tight">
            Chartink history
          </h1>
          <output className="block text-sm text-muted-foreground">
            {state === 'saving'
              ? 'Saving signals and opening your setup…'
              : state === 'error'
                ? 'Import needs attention'
                : 'Waiting for your scanner’s history…'}
          </output>
        </div>
        {error && (
          <div className="space-y-3">
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
            <Button onClick={() => setAttempt((value) => value + 1)}>Try again</Button>
          </div>
        )}
      </section>
    </main>
  )
}
