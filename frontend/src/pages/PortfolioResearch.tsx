import { useQuery, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { type PortfolioJob, type PortfolioSource, portfolioResearch } from '@/api/portfolioResearch'
import {
  addPortfolioSource,
  freshPortfolioDraft,
  PortfolioBuilder,
  type PortfolioDraft,
  portfolioDraftIssue,
  portfolioPayload,
} from '@/components/research/PortfolioBuilder'
import { PortfolioResults } from '@/components/research/PortfolioResults'
import {
  ResearchRunProgress,
  type ResearchUploadActivity,
} from '@/components/research/ResearchRunProgress'
import { StudyActivity } from '@/components/research/StudyActivity'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { researchNavigationReturn } from '@/hooks/useResearchNavigation'
import { useAuthStore } from '@/stores/authStore'

const active = (status?: string) =>
  ['queued', 'running', 'cancelling', 'cancel_requested'].includes(status ?? '')
const isPortfolio = (job: PortfolioJob) =>
  ['portfolio_backtest', 'portfolio_optimize'].includes(job.kind ?? '')
function message(error: unknown): string {
  return isAxiosError(error) && typeof error.response?.data?.message === 'string'
    ? error.response.data.message
    : error instanceof Error
      ? error.message
      : 'Something went wrong. Try again.'
}
function sourceName(source: PortfolioSource) {
  return (
    source.receipt.name ||
    source.receipt.filename ||
    `${source.receipt.signal_count.toLocaleString('en-IN')} ${source.receipt.signal_count === 1 ? 'signal' : 'signals'}`
  )
}
function sourceSavedAt(source: PortfolioSource) {
  if (!source.created_at || !Number.isFinite(source.created_at)) return null
  return new Date(source.created_at * 1000).toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
function readDraft(owner: string): PortfolioDraft {
  try {
    const saved = JSON.parse(sessionStorage.getItem(`portfolio-draft:${owner}`) ?? 'null')
    if (
      saved?.portfolio?.version === 'research-portfolio-v1' &&
      Array.isArray(saved.portfolio.strategies) &&
      saved.portfolio.strategies.length <= 8 &&
      typeof saved.optimizing === 'boolean' &&
      saved.optimization &&
      saved.sources &&
      saved.portfolio.strategies.every(
        (strategy: Record<string, unknown>) =>
          strategy.config && strategy.search && typeof strategy.id === 'string'
      )
    )
      return saved
  } catch {
    /* An unavailable browser store does not block research. */
  }
  return freshPortfolioDraft()
}
function requestId(owner: string, key: string): string {
  try {
    const saved = JSON.parse(sessionStorage.getItem(`portfolio-request:${owner}`) ?? 'null')
    if (saved?.key === key && typeof saved.id === 'string') return saved.id
  } catch {
    /* Use a fresh request identity when storage is unavailable. */
  }
  const id = crypto.randomUUID()
  try {
    sessionStorage.setItem(`portfolio-request:${owner}`, JSON.stringify({ key, id }))
  } catch {
    /* Native jobs still retain the request identity. */
  }
  return id
}
export default function PortfolioResearch({
  onLegacyJob,
  workspace,
  onUseSetup,
}: {
  onLegacyJob?: (jobId: string) => void
  workspace?: {
    experimentId?: string
    draft: PortfolioDraft
    onChange: (draft: PortfolioDraft) => void
    onRun: () => Promise<PortfolioJob>
    onOpenJob: (job: PortfolioJob, returnStudyId?: string) => void
    onReplay?: (
      jobId: string,
      trialId?: string,
      period?: 'selection' | 'evaluation'
    ) => Promise<PortfolioJob>
    onJobUpdate?: (job: PortfolioJob) => void
    disabled?: boolean
    readOnly?: boolean
  }
  onUseSetup?: (job: PortfolioJob, mode: 'backtest' | 'optimize', trialId?: string) => Promise<void>
}) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const jobId = params.get('job')
  const queryClient = useQueryClient()
  const [localDraft, setLocalDraft] = useState(() => readDraft(owner))
  const draft = workspace?.draft ?? localDraft
  const setDraft = workspace?.onChange ?? setLocalDraft
  const draftRef = useRef(draft)
  const priorOwner = useRef(owner)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [activityIdentity, setActivityIdentity] = useState<string | null>(null)
  const reportRequest = useRef<AbortController | null>(null)
  // Cancel an old report lookup when its account or originating study changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: these identities define the request lifetime.
  useEffect(
    () => () => {
      reportRequest.current?.abort()
    },
    [owner, jobId]
  )
  const [uploading, setUploading] = useState(false)
  const [uploadActivity, setUploadActivity] = useState<ResearchUploadActivity | undefined>()
  const [savedOpen, setSavedOpen] = useState(false)
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const [sourceOffset, setSourceOffset] = useState(0)
  const sourceOpener = useRef<HTMLButtonElement | null>(null)
  const sourceSelection = useRef(false)
  const [earlier, setEarlier] = useState(false)
  const [cursor, setCursor] = useState<string | undefined>()
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([])
  const current = useQuery({
    queryKey: ['portfolio-job', owner, jobId],
    queryFn: ({ signal }) => portfolioResearch.job(jobId!, signal),
    enabled: Boolean(jobId),
    retry: false,
    refetchInterval: (query) => (active(query.state.data?.status) ? 1500 : false),
  })
  const saved = useQuery({
    queryKey: ['portfolio-jobs', owner, cursor],
    queryFn: ({ signal }) => portfolioResearch.jobs({ page_size: 50, cursor }, signal),
    enabled: savedOpen,
    retry: false,
  })
  const onJobUpdate = workspace?.onJobUpdate
  useEffect(() => {
    if (current.data) onJobUpdate?.(current.data)
  }, [current.data, onJobUpdate])
  const sources = useQuery({
    queryKey: ['portfolio-sources', owner, sourceOffset],
    queryFn: ({ signal }) => portfolioResearch.sources(sourceOffset, signal),
    enabled: sourcesOpen && !jobId,
    retry: false,
  })
  const capabilities = useQuery({
    queryKey: ['portfolio-capabilities', owner],
    queryFn: ({ signal }) => portfolioResearch.capabilities(signal),
    enabled: !jobId,
    retry: false,
    staleTime: 60000,
  })
  useEffect(() => {
    if (workspace) {
      draftRef.current = draft
      return
    }
    if (priorOwner.current !== owner) {
      priorOwner.current = owner
      const next = readDraft(owner)
      draftRef.current = next
      setDraft(next)
      setSourcesOpen(false)
      sourceSelection.current = false
      return
    }
    draftRef.current = draft
    try {
      sessionStorage.setItem(`portfolio-draft:${owner}`, JSON.stringify(draft))
    } catch {
      /* Saving a run remains available without browser storage. */
    }
  }, [draft, owner, workspace, setDraft])
  const changeDraft = (next: PortfolioDraft) => {
    const retainedSources = new Set(next.portfolio.strategies.map((strategy) => strategy.source_id))
    const bounded = {
      ...next,
      sources: Object.fromEntries(
        Object.entries(next.sources).filter(([id]) => retainedSources.has(id))
      ),
    }
    draftRef.current = bounded
    setDraft(bounded)
    setError(null)
  }
  function openSources(opener: HTMLButtonElement) {
    if (busy || uploading || draftRef.current.portfolio.strategies.length >= 8) return
    sourceOpener.current = opener
    sourceSelection.current = false
    setSourceOffset(0)
    setSourcesOpen(true)
  }
  function chooseSource(source: PortfolioSource) {
    if (
      sourceSelection.current ||
      busy ||
      uploading ||
      priorOwner.current !== owner ||
      source.receipt.input_type === 'portfolio'
    )
      return
    sourceSelection.current = true
    changeDraft(addPortfolioSource(draftRef.current, source, sourceName(source)))
    setSourcesOpen(false)
  }
  function openJob(job: PortfolioJob, returnStudyId?: string) {
    setError(null)
    setSavedOpen(false)
    queryClient.setQueryData(['portfolio-job', owner, job.id], job)
    if (workspace) workspace.onOpenJob(job, returnStudyId)
    else setParams({ job: job.id, ...(returnStudyId ? { return_job: returnStudyId } : {}) })
    void queryClient.invalidateQueries({ queryKey: ['portfolio-job', owner, job.id] })
    void queryClient.invalidateQueries({ queryKey: ['portfolio-jobs', owner] })
  }
  function legacy(id: string) {
    setSavedOpen(false)
    if (onLegacyJob) onLegacyJob(id)
    else navigate(`/scanner-research?job=${encodeURIComponent(id)}&legacy=1`)
  }
  async function upload(files: File[]) {
    if (!files.length) return
    if (draftRef.current.portfolio.strategies.length + files.length > 8) {
      setError('A portfolio can contain up to eight strategies.')
      return
    }
    if (files.some((file) => file.size > 8 * 1024 * 1024)) {
      setError('Each CSV must be no larger than 8 MB.')
      return
    }
    setError(null)
    setUploading(true)
    let completed = 0
    let signals = 0
    try {
      for (const file of files) {
        setUploadActivity({ total: files.length, completed, signals, filename: file.name })
        const source = await portfolioResearch.upload(file)
        const next = addPortfolioSource(draftRef.current, source, file.name)
        draftRef.current = next
        setDraft(next)
        completed += 1
        signals += source.receipt.signal_count
      }
    } catch (cause) {
      setError(message(cause))
    } finally {
      setUploading(false)
      setUploadActivity(undefined)
    }
  }
  async function run() {
    const problem = portfolioDraftIssue(draftRef.current)
    if (problem) {
      setError(problem)
      return
    }
    setError(null)
    setBusy(true)
    try {
      if (workspace) {
        openJob(await workspace.onRun())
        return
      }
      const payload = portfolioPayload(draftRef.current)
      const checked = await portfolioResearch.preflight(payload)
      const job = await portfolioResearch.submit(
        checked.portfolio,
        requestId(owner, JSON.stringify(checked.portfolio))
      )
      openJob(job)
      try {
        sessionStorage.removeItem(`portfolio-request:${owner}`)
      } catch {
        /* Request identity is also retained by the server. */
      }
    } catch (cause) {
      setError(message(cause))
    } finally {
      setBusy(false)
    }
  }
  async function jobAction(action: 'cancel' | 'resume' | 'rerun' | 'evaluate', trialId?: string) {
    if (!jobId) return
    setError(null)
    setBusy(true)
    try {
      const job =
        action === 'rerun' || action === 'evaluate'
          ? workspace?.onReplay
            ? await workspace.onReplay(
                jobId,
                trialId,
                action === 'evaluate' ? 'evaluation' : undefined
              )
            : await portfolioResearch.rerun(
                jobId,
                requestId(owner, `${action}:${jobId}:${trialId ?? 'selected'}`),
                trialId,
                action === 'evaluate' ? 'evaluation' : undefined
              )
          : await portfolioResearch[action](jobId)
      openJob(job)
      if (action === 'rerun' || action === 'evaluate') {
        try {
          sessionStorage.removeItem(`portfolio-request:${owner}`)
        } catch {
          /* Server retains the identity. */
        }
      }
    } catch (cause) {
      setError(message(cause))
    } finally {
      setBusy(false)
    }
  }
  function newRun() {
    changeDraft(freshPortfolioDraft())
    setParams({})
  }
  function editSetup() {
    if (onUseSetup && current.data) {
      void reuseSetup('backtest')
      return
    }
    const portfolio = current.data?.specification?.portfolio
    if (portfolio)
      changeDraft({
        ...draft,
        portfolio,
        optimizing: Boolean(portfolio.optimization),
        optimization: portfolio.optimization ?? draft.optimization,
        equalWeights: false,
      })
    setParams({})
  }
  async function reuseSetup(mode: 'backtest' | 'optimize', trialId?: string) {
    if (!current.data || !onUseSetup) return
    setBusy(true)
    setError(null)
    try {
      await onUseSetup(current.data, mode, trialId)
    } catch (cause) {
      setError(message(cause))
    } finally {
      setBusy(false)
    }
  }
  const job = current.data
  const visibleError =
    error ??
    (current.error ? message(current.error) : null) ??
    (job?.error && job.status !== 'completed' ? job.error : null)
  const savedRows =
    saved.data?.items.filter((item) => (earlier ? !isPortfolio(item) : isPortfolio(item))) ?? []
  const Container = workspace ? 'section' : 'main'
  return (
    <Container
      className={
        workspace ? 'space-y-3' : 'mx-auto w-full max-w-6xl space-y-7 px-4 py-6 sm:px-6 sm:py-8'
      }
    >
      <style>
        {
          '@media (prefers-reduced-motion: reduce) { [data-slot="sheet-overlay"], [data-slot="sheet-content"], [data-slot="dialog-overlay"], [data-slot="dialog-content"] { animation: none !important; transition: none !important; } }'
        }
      </style>
      {!workspace && params.get('return_job') && (
        <Button
          variant="ghost"
          onClick={() => {
            const next = new URLSearchParams({ job: params.get('return_job')! })
            setParams(next, { state: researchNavigationReturn(owner, null, next) })
          }}
        >
          Back to study
        </Button>
      )}
      {!workspace && (
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Backtest & Optimize</h1>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={busy || uploading}
              onClick={() => {
                setSavedOpen(true)
                setError(null)
              }}
            >
              Saved runs
            </Button>
            {(jobId || draft.portfolio.strategies.length > 0) && (
              <Button type="button" variant="ghost" disabled={busy || uploading} onClick={newRun}>
                New run
              </Button>
            )}
          </div>
        </header>
      )}
      {visibleError && (
        <div
          role="alert"
          className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive"
        >
          {visibleError}
        </div>
      )}
      {!jobId && (
        <PortfolioBuilder
          draft={draft}
          onChange={changeDraft}
          onUpload={(files) => {
            void upload(files)
          }}
          onUseSaved={openSources}
          onRun={() => {
            void run()
          }}
          busy={busy || workspace?.disabled === true}
          uploading={uploading}
          uploadActivity={uploadActivity}
          capabilities={capabilities.data}
        />
      )}
      {jobId && current.isLoading && (
        <output className="block py-12 text-center text-sm text-muted-foreground">
          Opening saved run…
        </output>
      )}
      {job && !isPortfolio(job) && (
        <div className="space-y-3 py-6">
          <p className="text-sm text-muted-foreground">
            This run uses the earlier scanner workflow.
          </p>
          <Button type="button" onClick={() => legacy(job.id)}>
            Open earlier run
          </Button>
        </div>
      )}
      {job && isPortfolio(job) && job.status === 'completed' && job.result && (
        <PortfolioResults
          key={job.id}
          job={job}
          result={job.result}
          experimentId={workspace?.experimentId}
          studyReport={params.get('report') === 'best'}
          hideStudyBack={params.has('return_shortlist') || params.has('return_research')}
          onStudyReportChange={(open) => {
            const next = new URLSearchParams(params)
            if (open) next.set('report', 'best')
            else next.delete('report')
            setParams(
              next,
              !open
                ? { state: researchNavigationReturn(owner, workspace?.experimentId, next) }
                : undefined
            )
          }}
          onOpenReport={(reportJobId) => {
            reportRequest.current?.abort()
            const controller = new AbortController()
            reportRequest.current = controller
            void portfolioResearch
              .job(reportJobId, controller.signal)
              .then((reportJob) => {
                if (controller.signal.aborted) return
                openJob(reportJob, job.id)
              })
              .catch((reason) => {
                if (!controller.signal.aborted)
                  setError(
                    reason instanceof Error ? reason.message : 'The report could not be opened.'
                  )
              })
              .finally(() => {
                if (reportRequest.current === controller) reportRequest.current = null
              })
          }}
          embedded={Boolean(workspace)}
          onEvaluate={() => {
            void jobAction('evaluate')
          }}
          onRerun={(trialId) => {
            void jobAction('rerun', trialId)
          }}
          rerunning={busy}
          exportUrl={portfolioResearch.exportUrl(job.id)}
          onAdjust={
            onUseSetup
              ? (trialId) => {
                  void reuseSetup('backtest', trialId)
                }
              : undefined
          }
          onOptimize={
            onUseSetup
              ? () => {
                  void reuseSetup('optimize')
                }
              : undefined
          }
          readOnly={workspace?.readOnly}
        />
      )}
      {job && isPortfolio(job) && job.status !== 'completed' && (
        <section className="mx-auto max-w-3xl space-y-6 py-6" aria-label="Portfolio run progress">
          <h2 className="text-xl font-semibold">
            {job.specification?.portfolio?.name ?? 'Portfolio run'}
          </h2>
          <ResearchRunProgress job={job} />
          <div className="flex gap-2">
            {active(job.status) ? (
              <Button
                type="button"
                variant="outline"
                disabled={busy || job.status === 'cancel_requested' || job.status === 'cancelling'}
                onClick={() => {
                  void jobAction('cancel')
                }}
              >
                Cancel run
              </Button>
            ) : (
              <>
                {job.resumable && !workspace?.readOnly && (
                  <Button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      void jobAction('resume')
                    }}
                  >
                    Resume run
                  </Button>
                )}
                {!workspace?.readOnly && (
                  <Button type="button" variant="outline" onClick={editSetup}>
                    Edit setup
                  </Button>
                )}
              </>
            )}
            {job.kind === 'portfolio_optimize' && (
              <Button
                type="button"
                variant="ghost"
                aria-expanded={activityIdentity === JSON.stringify([owner, job.id])}
                aria-controls="portfolio-study-activity"
                onClick={() =>
                  setActivityIdentity((previous) =>
                    previous === JSON.stringify([owner, job.id])
                      ? null
                      : JSON.stringify([owner, job.id])
                  )
                }
              >
                Activity
              </Button>
            )}
          </div>
          {job.kind === 'portfolio_optimize' &&
            activityIdentity === JSON.stringify([owner, job.id]) && (
              <div id="portfolio-study-activity" className="border-t pt-6">
                <StudyActivity
                  jobId={job.id}
                  jobStatus={job.status}
                  strategies={job.specification?.portfolio?.strategies}
                />
              </div>
            )}
        </section>
      )}
      <Dialog open={sourcesOpen} onOpenChange={setSourcesOpen}>
        <DialogContent
          className="max-h-[85vh] overflow-y-auto sm:max-w-xl"
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            if (sourceOpener.current?.isConnected) sourceOpener.current.focus()
            else document.getElementById('portfolio-saved-signals')?.focus()
          }}
        >
          <DialogHeader>
            <DialogTitle>Saved signals</DialogTitle>
            <DialogDescription className="sr-only">
              Choose a saved signal file to add to this portfolio.
            </DialogDescription>
          </DialogHeader>
          {sources.isLoading ? (
            <output className="block py-6 text-sm text-muted-foreground">
              Loading saved signals…
            </output>
          ) : sources.error ? (
            <div className="space-y-3">
              <p role="alert" className="text-sm text-destructive">
                {message(sources.error)}
              </p>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  void sources.refetch()
                }}
              >
                Try again
              </Button>
            </div>
          ) : (
            <div className="divide-y">
              {sources.data?.items
                .filter((source) => source.receipt.input_type !== 'portfolio')
                .map((source) => (
                  <button
                    type="button"
                    key={source.id}
                    disabled={draft.portfolio.strategies.length >= 8}
                    className="flex w-full items-center justify-between gap-4 rounded-sm py-4 text-left hover:text-primary focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50"
                    onClick={() => chooseSource(source)}
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-medium" title={sourceName(source)}>
                        {sourceName(source)}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {source.receipt.date_from}
                        {source.receipt.date_from !== source.receipt.date_to &&
                          ` – ${source.receipt.date_to}`}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {sourceSavedAt(source)
                          ? `Saved ${sourceSavedAt(source)}`
                          : `Saved input ${source.id.slice(0, 8)}`}
                      </span>
                    </span>
                    <span className="shrink-0 text-right text-xs text-muted-foreground">
                      {(source.receipt.name || source.receipt.filename) && (
                        <span className="block">
                          {source.receipt.signal_count.toLocaleString('en-IN')}{' '}
                          {source.receipt.signal_count === 1 ? 'signal' : 'signals'}
                        </span>
                      )}
                      <span className="mt-1 block">
                        {source.receipt.symbol_count.toLocaleString('en-IN')}{' '}
                        {source.receipt.symbol_count === 1 ? 'symbol' : 'symbols'}
                      </span>
                    </span>
                  </button>
                ))}
              {!sources.data?.items.some((source) => source.receipt.input_type !== 'portfolio') && (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No saved signals on this page. Upload a CSV to add a strategy.
                </p>
              )}
            </div>
          )}
          {(sourceOffset > 0 || sources.data?.next_offset != null) && (
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={sourceOffset === 0 || sources.isFetching}
                onClick={() => setSourceOffset(Math.max(0, sourceOffset - 20))}
              >
                Previous
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={sources.data?.next_offset == null || sources.isFetching}
                onClick={() => setSourceOffset(sources.data?.next_offset ?? sourceOffset)}
              >
                More signals
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
      <Dialog open={savedOpen} onOpenChange={setSavedOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Saved runs</DialogTitle>
            <DialogDescription className="sr-only">
              Open a saved portfolio or an earlier scanner run.
            </DialogDescription>
          </DialogHeader>
          <fieldset className="flex gap-2" aria-label="Saved run category">
            <Button
              type="button"
              size="sm"
              variant={!earlier ? 'secondary' : 'ghost'}
              aria-pressed={!earlier}
              onClick={() => setEarlier(false)}
            >
              Portfolios
            </Button>
            <Button
              type="button"
              size="sm"
              variant={earlier ? 'secondary' : 'ghost'}
              aria-pressed={earlier}
              onClick={() => setEarlier(true)}
            >
              Earlier runs
            </Button>
          </fieldset>
          {saved.isLoading ? (
            <output className="block py-6 text-sm text-muted-foreground">Loading runs…</output>
          ) : saved.error ? (
            <p role="alert" className="text-sm text-destructive">
              {message(saved.error)}
            </p>
          ) : (
            <div className="divide-y">
              {savedRows.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className="flex w-full items-center justify-between gap-4 py-4 text-left hover:text-primary focus-visible:underline"
                  onClick={() => (isPortfolio(item) ? openJob(item) : legacy(item.id))}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">
                      {item.specification?.portfolio?.name ??
                        item.source_summary?.name ??
                        item.title ??
                        'Saved run'}
                    </span>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {item.source_summary?.date_from}
                      {item.source_summary?.date_to ? ` – ${item.source_summary.date_to}` : ''}
                      {item.kind === 'portfolio_optimize'
                        ? ' · Optimization'
                        : item.kind === 'portfolio_backtest'
                          ? ' · Backtest'
                          : ''}
                    </span>
                  </span>
                  <span className="text-xs capitalize text-muted-foreground">{item.status}</span>
                </button>
              ))}
              {!savedRows.length && (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No {earlier ? 'earlier' : 'portfolio'} runs on this page.
                </p>
              )}
            </div>
          )}
          {(cursor || saved.data?.next_cursor) && (
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!cursorHistory.length}
                onClick={() => {
                  setCursor(cursorHistory.at(-1))
                  setCursorHistory((history) => history.slice(0, -1))
                }}
              >
                Previous
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!saved.data?.next_cursor}
                onClick={() => {
                  setCursorHistory((history) => [...history, cursor])
                  setCursor(saved.data?.next_cursor ?? undefined)
                }}
              >
                More runs
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </Container>
  )
}
