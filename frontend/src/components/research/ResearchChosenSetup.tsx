import { useQuery, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import {
  type ChosenSetup,
  chosenSetupKey,
  type ReuseSetupRequest,
  researchChosenSetups,
} from '@/api/researchChosenSetups'
import type { DecisionEvent } from '@/api/researchDecisions'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { researchError } from '@/hooks/useResearchExperiment'
import { researchReturnLocation } from '@/hooks/useResearchNavigation'
import { useResearchWorkspaceMutations } from '@/hooks/useResearchWorkspaceMutations'
import { useAuthStore } from '@/stores/authStore'
import { ReuseChosenSetup } from './ReuseChosenSetup'
import { researchDates, strategyRuleSummary } from './researchPresentation'

export function ChooseResearchSetup(props: {
  experimentId: string
  event: DecisionEvent
  readOnly: boolean
}) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  if (props.event.state !== 'keep') return null
  return (
    <ChooseSetup
      key={`${owner}:${props.experimentId}:${props.event.id}`}
      {...props}
      owner={owner}
    />
  )
}
function ChooseSetup({
  owner,
  experimentId,
  event,
  readOnly,
}: {
  owner: string
  experimentId: string
  event: DecisionEvent
  readOnly: boolean
}) {
  const client = useQueryClient()
  const { beforeChange, afterChoice } = useResearchWorkspaceMutations()
  const [, setParams] = useSearchParams()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(event.candidate_name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [accepted, setAccepted] = useState(false)
  const committed = useRef(false)
  const definitiveConflict = useRef(false)
  const request = useRef<{
    signature: string
    body: {
      decision_id: string
      event_id: string
      revision: number
      name: string
      request_id: string
    }
  } | null>(null)
  const active = useRef<AbortController | null>(null)
  const target = { decision_id: event.decision_id, event_id: event.id }
  const key = [...chosenSetupKey(owner, experimentId), 'eligibility', event.id]
  const context = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => researchChosenSetups.context(experimentId, target, signal),
    enabled: open,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  })
  useEffect(() => () => active.current?.abort(), [])
  useEffect(() => {
    if (readOnly) {
      active.current?.abort()
      active.current = null
      setBusy(false)
    }
  }, [readOnly])
  const data = context.data
  function close(value: boolean) {
    active.current?.abort()
    active.current = null
    setBusy(false)
    setError(null)
    setOpen(value)
    if (!value) void client.cancelQueries({ queryKey: key })
  }
  async function begin() {
    if (readOnly || active.current) return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      await beforeChange()
      if (!controller.signal.aborted) setOpen(true)
    } catch (cause) {
      if (!controller.signal.aborted) setError(researchError(cause))
    } finally {
      if (active.current === controller) {
        active.current = null
        setBusy(false)
      }
    }
  }
  function refresh() {
    if (definitiveConflict.current && !committed.current) {
      request.current = null
      definitiveConflict.current = false
    }
    void context.refetch()
  }
  async function choose() {
    if (
      readOnly ||
      active.current ||
      (!committed.current &&
        (!data?.eligibility?.available ||
          data.archived ||
          context.isFetching ||
          context.isError ||
          !name.trim()))
    )
      return
    if (!committed.current) {
      const specification = { ...target, name: name.trim() }
      const signature = JSON.stringify(specification)
      if (request.current?.signature !== signature)
        request.current = {
          signature,
          body: { ...specification, revision: data!.revision, request_id: crypto.randomUUID() },
        }
    }
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      if (!committed.current) {
        await researchChosenSetups.choose(experimentId, request.current!.body, controller.signal)
        if (controller.signal.aborted) return
        committed.current = true
        setAccepted(true)
      }
      await afterChoice()
      if (controller.signal.aborted) return
      void client.invalidateQueries({ queryKey: chosenSetupKey(owner, experimentId) })
      void client.invalidateQueries({ queryKey: ['research-library', owner] })
      setOpen(false)
      setParams({ experiment: experimentId, view: 'overview' })
    } catch (cause) {
      if (!controller.signal.aborted) {
        definitiveConflict.current =
          isAxiosError(cause) && cause.response?.data?.code === 'revision_conflict'
        setError(researchError(cause))
      }
    } finally {
      if (active.current === controller) {
        active.current = null
        setBusy(false)
      }
    }
  }
  return (
    <>
      <Button size="sm" variant="outline" disabled={readOnly || busy} onClick={() => void begin()}>
        Choose setup
      </Button>
      {!open && error && (
        <p role="alert" className="text-sm">
          {error}
        </p>
      )}
      <Dialog open={open} onOpenChange={close}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Choose a setup to reuse</DialogTitle>
            <DialogDescription>
              Keep its rules and the evidence behind your decision together.
            </DialogDescription>
          </DialogHeader>
          {context.isPending && (
            <p className="text-sm text-muted-foreground">Opening saved rules…</p>
          )}
          {context.isError && (
            <p role="alert" className="text-sm">
              {researchError(context.error)}{' '}
              <Button size="sm" variant="link" onClick={() => void context.refetch()}>
                Retry
              </Button>
            </p>
          )}
          {data?.eligibility && (
            <form
              className="space-y-5"
              onSubmit={(e) => {
                e.preventDefault()
                void choose()
              }}
            >
              <div className="space-y-2">
                <Label htmlFor="chosen-setup-name">Setup name</Label>
                <Input
                  id="chosen-setup-name"
                  value={name}
                  maxLength={120}
                  disabled={busy || readOnly || accepted}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="space-y-2 text-sm">
                <p className="font-medium">{data.eligibility.candidate_name}</p>
                <p className="text-xs text-muted-foreground">
                  {researchDates(data.eligibility.dates.from, data.eligibility.dates.to)}
                </p>
                {data.eligibility.strategies.map((strategy) => (
                  <p key={strategy.id}>
                    <span className="font-medium">{strategy.name}</span>
                    <br />
                    <span className="text-xs text-muted-foreground">
                      {strategyRuleSummary(strategy)} · {strategy.allocation_pct}% allocation
                    </span>
                  </p>
                ))}
              </div>
              {data.current && data.current.event_id !== event.id && (
                <p className="text-xs text-muted-foreground">
                  Replaces {data.current.name} as your current choice. Earlier choices stay saved.
                </p>
              )}
              {!data.eligibility.available && (
                <p className="text-sm text-muted-foreground">{data.eligibility.reason}</p>
              )}
              {error && (
                <p role="alert" className="text-sm">
                  {error}{' '}
                  <Button type="button" size="sm" variant="link" onClick={refresh}>
                    Refresh
                  </Button>
                </p>
              )}
              <div className="flex justify-end">
                <Button
                  type="submit"
                  disabled={
                    busy ||
                    readOnly ||
                    (!accepted &&
                      (data.archived ||
                        !name.trim() ||
                        !data.eligibility.available ||
                        context.isFetching ||
                        context.isError))
                  }
                >
                  {busy ? 'Saving…' : accepted ? 'Open overview' : 'Choose setup'}
                </Button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

export function ResearchChosenSetup({
  experimentId,
  readOnly,
  onUse,
  onReplay,
}: {
  experimentId: string
  readOnly: boolean
  onUse: (request: ReuseSetupRequest, signal: AbortSignal) => Promise<void>
  onReplay: (
    choice: ChosenSetup,
    revision: number,
    requestId: string,
    signal: AbortSignal
  ) => Promise<void>
}) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  return (
    <ChosenSetupPanel
      key={`${owner}:${experimentId}`}
      owner={owner}
      experimentId={experimentId}
      readOnly={readOnly}
      onUse={onUse}
      onReplay={onReplay}
    />
  )
}
function ChosenSetupPanel({
  owner,
  experimentId,
  readOnly,
  onUse,
  onReplay,
}: {
  owner: string
  experimentId: string
  readOnly: boolean
  onUse: (request: ReuseSetupRequest, signal: AbortSignal) => Promise<void>
  onReplay: (
    choice: ChosenSetup,
    revision: number,
    requestId: string,
    signal: AbortSignal
  ) => Promise<void>
}) {
  const [params, setParams] = useSearchParams()
  const { beforeChange } = useResearchWorkspaceMutations()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [offset, setOffset] = useState(0)
  const active = useRef<AbortController | null>(null)
  const request = useRef<{
    choice: ChosenSetup
    body: { choice_id: string; revision: number; request_id: string }
  } | null>(null)
  const definitiveConflict = useRef(false)
  const activeChoice = useRef<string | undefined>(undefined)
  const context = useQuery({
    queryKey: [...chosenSetupKey(owner, experimentId), 'current'],
    queryFn: ({ signal }) => researchChosenSetups.context(experimentId, undefined, signal),
    gcTime: 0,
    retry: false,
  })
  const history = useQuery({
    queryKey: [...chosenSetupKey(owner, experimentId), 'history', offset],
    queryFn: ({ signal }) => researchChosenSetups.history(experimentId, offset, signal),
    enabled: showHistory,
    gcTime: 0,
    retry: false,
  })
  useEffect(() => () => active.current?.abort(), [])
  const choice = context.data?.current
  useEffect(() => {
    if (activeChoice.current === choice?.id) return
    activeChoice.current = choice?.id
    active.current?.abort()
    active.current = null
    request.current = null
    setBusy(false)
    setError(null)
  }, [choice?.id])
  async function replay() {
    if (!choice?.usable || readOnly || active.current || context.isFetching || context.isError)
      return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      if (!request.current) {
        await beforeChange()
        if (controller.signal.aborted) return
        const refreshed = await context.refetch()
        if (controller.signal.aborted) return
        if (refreshed.isError) throw refreshed.error
        const latest = refreshed.data
        const current = latest?.current
        if (!latest || current?.id !== choice.id || !current.usable || latest.archived)
          throw new Error('The chosen setup changed. Review its current choice before replaying.')
        request.current = {
          choice: current,
          body: {
            choice_id: current.id,
            revision: latest.revision,
            request_id: crypto.randomUUID(),
          },
        }
      }
      const pending = request.current
      await onReplay(
        pending.choice,
        pending.body.revision,
        pending.body.request_id,
        controller.signal
      )
    } catch (cause) {
      if (!controller.signal.aborted) {
        definitiveConflict.current =
          isAxiosError(cause) && cause.response?.data?.code === 'revision_conflict'
        setError(researchError(cause))
      }
    } finally {
      if (active.current === controller) {
        active.current = null
        setBusy(false)
      }
    }
  }
  if (context.isError)
    return (
      <p role="alert" className="text-sm">
        Chosen setup could not be opened.{' '}
        <Button size="sm" variant="link" onClick={() => void context.refetch()}>
          Retry
        </Button>
      </p>
    )
  if (!choice) return null
  return (
    <section aria-label="Chosen setup" className="space-y-3 rounded-lg border p-5">
      <p className="text-xs text-muted-foreground">Chosen setup</p>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-2">
          <h2 className="break-words font-semibold">{choice.name}</h2>
          <p className="text-xs text-muted-foreground">
            {researchDates(choice.dates.from, choice.dates.to)} · Version {choice.version.number}
          </p>
          {choice.strategies.map((strategy) => (
            <p key={strategy.id} className="text-sm">
              <span className="font-medium">{strategy.name}</span> ·{' '}
              <span className="text-muted-foreground">{strategyRuleSummary(strategy)}</span>
            </p>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <ReuseChosenSetup
            key={choice.id}
            owner={owner}
            experimentId={experimentId}
            choice={choice}
            readOnly={readOnly || busy}
            onUse={onUse}
          />
          <Button
            size="sm"
            variant="outline"
            disabled={busy || readOnly || !choice.usable || context.isFetching}
            onClick={() => void replay()}
          >
            {busy ? 'Preparing replay…' : 'Replay exact'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              setParams({
                experiment: experimentId,
                view: 'decisions',
                decision: choice.decision_id,
                decision_event: choice.event_id,
                return_research: researchReturnLocation(params, experimentId),
              })
            }
          >
            Review evidence
          </Button>
        </div>
      </div>
      {!choice.usable && (
        <p className="text-sm text-muted-foreground">{choice.unavailable_reason}</p>
      )}
      {error && (
        <p role="alert" className="text-sm">
          {error}
          {definitiveConflict.current && (
            <Button
              size="sm"
              variant="link"
              onClick={() => {
                request.current = null
                definitiveConflict.current = false
                setError(null)
                void context.refetch()
              }}
            >
              Refresh
            </Button>
          )}
        </p>
      )}
      {choice.sequence > 1 && (
        <details onToggle={(e) => setShowHistory(e.currentTarget.open)}>
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Previous choices
          </summary>
          <div className="mt-3 divide-y">
            {history.isPending && <p className="py-2 text-sm">Opening choices…</p>}
            {history.isError && (
              <Button size="sm" variant="link" onClick={() => void history.refetch()}>
                Retry
              </Button>
            )}
            {history.data?.items.map((item) => (
              <div
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
              >
                <span>
                  {item.name}
                  {item.id === choice.id ? ' · Current' : ''}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setParams({
                      experiment: experimentId,
                      view: 'decisions',
                      decision: item.decision_id,
                      decision_event: item.event_id,
                      return_research: researchReturnLocation(params, experimentId),
                    })
                  }
                >
                  Review evidence
                </Button>
              </div>
            ))}
            {(offset > 0 || history.data?.next_offset != null) && (
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 20))}
                >
                  Previous
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={history.data?.next_offset == null}
                  onClick={() => setOffset(history.data?.next_offset ?? offset)}
                >
                  More choices
                </Button>
              </div>
            )}
          </div>
        </details>
      )}
    </section>
  )
}
