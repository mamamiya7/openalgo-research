import { useQuery, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { useEffect, useRef, useState } from 'react'
import { portfolioResearch } from '@/api/portfolioResearch'
import {
  type ChosenSetup,
  chosenSetupKey,
  type ReuseSetupRequest,
  type ReuseSetupSpec,
  researchChosenSetups,
} from '@/api/researchChosenSetups'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { researchError } from '@/hooks/useResearchExperiment'
import { useResearchWorkspaceMutations } from '@/hooks/useResearchWorkspaceMutations'
import type { PortfolioDraft } from './PortfolioBuilder'
import { researchDates, strategyRuleSummary } from './researchPresentation'

export function ReuseChosenSetup({
  owner,
  experimentId,
  choice,
  readOnly,
  onUse,
}: {
  owner: string
  experimentId: string
  choice: ChosenSetup
  readOnly: boolean
  onUse: (request: ReuseSetupRequest, signal: AbortSignal) => Promise<void>
}) {
  const client = useQueryClient()
  const { beforeChange } = useResearchWorkspaceMutations()
  const [mode, setMode] = useState<'backtest' | 'optimize' | null>(null)
  const [sources, setSources] = useState<Record<string, string>>({})
  const [offset, setOffset] = useState(0)
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastDraft, setLastDraft] = useState<PortfolioDraft | null>(null)
  const active = useRef<AbortController | null>(null)
  const request = useRef<{ signature: string; body: ReuseSetupRequest } | null>(null)
  const definitiveConflict = useRef(false)
  const spec: ReuseSetupSpec = { choice_id: choice.id, mode: mode ?? 'backtest', sources }
  const key = [...chosenSetupKey(owner, experimentId), 'reuse', spec]
  const preview = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => researchChosenSetups.preview(experimentId, spec, signal),
    enabled: Boolean(mode),
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const files = useQuery({
    queryKey: ['research-reuse-sources', owner, offset],
    queryFn: ({ signal }) => portfolioResearch.sources(offset, signal),
    enabled: Boolean(mode),
    gcTime: 0,
    retry: false,
  })
  useEffect(() => {
    if (preview.data) setLastDraft(preview.data.draft)
  }, [preview.data])
  useEffect(() => () => active.current?.abort(), [])
  useEffect(() => {
    if (readOnly) {
      active.current?.abort()
      active.current = null
      setBusy(false)
      setUploading(null)
    }
  }, [readOnly])
  function close() {
    active.current?.abort()
    active.current = null
    void client.cancelQueries({ queryKey: [...chosenSetupKey(owner, experimentId), 'reuse'] })
    void client.cancelQueries({ queryKey: ['research-reuse-sources', owner] })
    setMode(null)
    setBusy(false)
    setUploading(null)
    setError(null)
    setSources({})
    setOffset(0)
    setLastDraft(null)
  }
  async function begin(nextMode: 'backtest' | 'optimize') {
    if (readOnly || active.current) return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      await beforeChange()
      if (!controller.signal.aborted) setMode(nextMode)
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
    if (definitiveConflict.current) {
      request.current = null
      definitiveConflict.current = false
    }
    void preview.refetch()
  }
  async function upload(strategy: string, file: File) {
    if (readOnly || active.current) return
    const controller = new AbortController()
    active.current = controller
    setUploading(strategy)
    setError(null)
    try {
      const source = await portfolioResearch.upload(file, controller.signal)
      if (controller.signal.aborted) return
      setSources((previous) => ({ ...previous, [strategy]: source.id }))
      void client.invalidateQueries({ queryKey: ['research-reuse-sources', owner] })
    } catch (cause) {
      if (!controller.signal.aborted) setError(researchError(cause))
    } finally {
      if (active.current === controller) {
        active.current = null
        setUploading(null)
      }
    }
  }
  async function apply() {
    if (
      !preview.data ||
      !preview.data.choice.usable ||
      preview.data.archived ||
      preview.isFetching ||
      preview.isError ||
      active.current ||
      readOnly
    )
      return
    const signature = JSON.stringify(spec)
    if (request.current?.signature !== signature)
      request.current = {
        signature,
        body: { ...spec, revision: preview.data.revision, request_id: crypto.randomUUID() },
      }
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    try {
      await onUse(request.current.body, controller.signal)
      if (!controller.signal.aborted) close()
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
  const draft = preview.data?.draft ?? lastDraft
  return (
    <>
      <Button
        size="sm"
        disabled={readOnly || busy || !choice.usable}
        onClick={() => void begin('backtest')}
      >
        Use setup
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={readOnly || busy || !choice.usable}
        onClick={() => void begin('optimize')}
      >
        Refine search
      </Button>
      {!mode && error && (
        <p role="alert" className="text-sm">
          {error}
        </p>
      )}
      <Dialog
        open={Boolean(mode)}
        onOpenChange={(value) => {
          if (!value) close()
        }}
      >
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {mode === 'optimize' ? 'Refine this setup' : 'Reuse this setup'}
            </DialogTitle>
            <DialogDescription>
              {choice.name} · Review the inputs, then open Setup.
            </DialogDescription>
          </DialogHeader>
          {preview.isPending && (
            <p className="text-sm text-muted-foreground">Checking saved inputs…</p>
          )}
          {preview.isError && (
            <p role="alert" className="text-sm">
              {researchError(preview.error)}{' '}
              <Button size="sm" variant="link" onClick={() => void preview.refetch()}>
                Retry
              </Button>
            </p>
          )}
          {draft && (
            <div className="space-y-5">
              {draft.portfolio.strategies.map((strategy) => {
                const selectedId = sources[strategy.id] ?? strategy.source_id
                const originalId = choice.strategies.find(
                  (item) => item.id === strategy.id
                )?.source_id
                const source =
                  draft.sources[selectedId] ??
                  files.data?.items.find((item) => item.id === selectedId)
                const currentName =
                  draft.sources[selectedId]?.filename ||
                  source?.receipt.filename ||
                  'Selected signal file'
                return (
                  <div key={strategy.id} className="space-y-2">
                    <Label htmlFor={`reuse-source-${strategy.id}`}>{strategy.name}</Label>
                    <p className="text-xs text-muted-foreground">{strategyRuleSummary(strategy)}</p>
                    <div className="flex flex-wrap items-center gap-2">
                      <select
                        id={`reuse-source-${strategy.id}`}
                        aria-label={`Signals for ${strategy.name}`}
                        disabled={busy || Boolean(uploading) || readOnly}
                        className="min-w-0 flex-1 rounded-md border bg-background px-3 py-2 text-sm"
                        value={selectedId}
                        onChange={(e) => {
                          setSources((previous) => ({ ...previous, [strategy.id]: e.target.value }))
                          setError(null)
                        }}
                      >
                        <option value={selectedId}>{currentName}</option>
                        {originalId &&
                          originalId !== selectedId &&
                          !files.data?.items.some((item) => item.id === originalId) && (
                            <option value={originalId}>Original saved signals</option>
                          )}
                        {files.data?.items
                          .filter((item) => item.id !== selectedId)
                          .map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.receipt.name || item.receipt.filename || 'Saved signals'} ·{' '}
                              {item.receipt.signal_count.toLocaleString('en-IN')} signals
                            </option>
                          ))}
                      </select>
                      <Label
                        className={`inline-flex h-9 items-center rounded-md border px-3 text-sm focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ring ${busy || uploading || readOnly ? 'opacity-50' : 'cursor-pointer'}`}
                      >
                        {uploading === strategy.id ? 'Reading CSV…' : 'Replace CSV'}
                        <input
                          type="file"
                          accept=".csv,text/csv"
                          className="sr-only"
                          aria-label={`Replace CSV for ${strategy.name}`}
                          disabled={busy || Boolean(uploading) || readOnly}
                          onChange={(e) => {
                            const file = e.target.files?.[0]
                            e.target.value = ''
                            if (file) void upload(strategy.id, file)
                          }}
                        />
                      </Label>
                    </div>
                    {source && (
                      <p className="text-xs text-muted-foreground">
                        {source.receipt.signal_count.toLocaleString('en-IN')} signals ·{' '}
                        {researchDates(source.receipt.date_from, source.receipt.date_to)}
                      </p>
                    )}
                  </div>
                )
              })}
              {(offset > 0 || files.data?.next_offset != null) && (
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - 20))}
                  >
                    Previous files
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={files.data?.next_offset == null}
                    onClick={() => setOffset(files.data?.next_offset ?? offset)}
                  >
                    More files
                  </Button>
                </div>
              )}
              {files.isError && (
                <p className="text-xs text-muted-foreground">
                  Saved files could not be loaded.{' '}
                  <Button size="sm" variant="link" onClick={() => void files.refetch()}>
                    Retry
                  </Button>
                </p>
              )}
              {preview.data && preview.data.changes.sources.length > 0 && (
                <section
                  aria-label="Input changes"
                  className="space-y-2 border-t pt-3 text-xs text-muted-foreground"
                >
                  {preview.data.changes.sources.map((change) => (
                    <p key={change.strategy_id}>
                      <span className="font-medium text-foreground">{change.name}</span>
                      <br />
                      {researchDates(change.before.date_from, change.before.date_to)} ·{' '}
                      {change.before.signal_count.toLocaleString('en-IN')} signals →{' '}
                      {researchDates(change.after.date_from, change.after.date_to)} ·{' '}
                      {change.after.signal_count.toLocaleString('en-IN')} signals
                    </p>
                  ))}
                  <p>Trading rules and allocations unchanged.</p>
                </section>
              )}
              {preview.data?.changes.fields.some((field) =>
                ['date_from', 'date_to'].includes(field.key)
              ) && (
                <p className="text-xs text-muted-foreground">
                  Date range: all signals in the selected files.
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                {mode === 'optimize' ? 'Choose which settings to search in Setup. ' : ''}Your
                current draft stays saved as a version.
              </p>
              {preview.data && !preview.data.choice.usable && (
                <p className="text-sm text-muted-foreground">
                  {preview.data.choice.unavailable_reason}
                </p>
              )}
              {error && (
                <p role="alert" className="text-sm">
                  {error}{' '}
                  <Button size="sm" variant="link" onClick={refresh}>
                    Refresh
                  </Button>
                </p>
              )}
              <div className="flex justify-end">
                <Button
                  disabled={
                    busy ||
                    Boolean(uploading) ||
                    readOnly ||
                    !preview.data ||
                    preview.data.archived ||
                    !preview.data.choice.usable ||
                    preview.isFetching ||
                    preview.isError
                  }
                  onClick={() => void apply()}
                >
                  {busy ? 'Opening…' : 'Open setup'}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
