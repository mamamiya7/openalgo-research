import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArrowLeft, ArrowUpRight, MoreHorizontal, Pin, Plus, Search } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { type PortfolioJob, type PortfolioSource, portfolioResearch } from '@/api/portfolioResearch'
import {
  type ExperimentSummary,
  type LibraryJob,
  type ResearchExperiment,
  researchLibrary,
  type SetupVersionSummary,
} from '@/api/researchLibrary'
import { ChartinkImport } from '@/components/research/ChartinkImport'
import { ChartinkSource } from '@/components/research/ChartinkSource'
import { addPortfolioSource, freshPortfolioDraft } from '@/components/research/PortfolioBuilder'
import { ResearchComparisons } from '@/components/research/ResearchComparisons'
import { researchRunStatus } from '@/components/research/ResearchRunProgress'
import { ResearchShortlist } from '@/components/research/ResearchShortlist'
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
import { Textarea } from '@/components/ui/textarea'
import { researchError, useResearchExperiment } from '@/hooks/useResearchExperiment'
import PortfolioResearch from '@/pages/PortfolioResearch'
import { useAuthStore } from '@/stores/authStore'

const date = (time: number | string) => {
  const parsed = typeof time === 'number' ? time * 1000 : time
  const value = new Date(parsed)
  return Number.isNaN(value.getTime())
    ? ''
    : value.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
}
const count = (value: number, word: string) => `${value} ${word}${value === 1 ? '' : 's'}`
const active = (job: PortfolioJob) =>
  ['queued', 'running', 'cancelling', 'cancel_requested'].includes(job.status)
const nonnegative = (value: string | null) =>
  Math.max(0, Math.min(100000, Number.parseInt(value ?? '0', 10) || 0))
const label = (job: PortfolioJob) =>
  job.specification?.portfolio?.name ?? job.source_summary?.name ?? job.title ?? 'Saved run'
const sourceLabel = (source: PortfolioSource) =>
  source.receipt.name || source.receipt.filename || count(source.receipt.signal_count, 'signal')
const tabs = [
  ['experiments', 'Experiments'],
  ['studies', 'Studies'],
  ['setups', 'Setups'],
  ['sources', 'Sources'],
  ['archived', 'Archived'],
  ['previous', 'Previous runs'],
] as const
function ErrorNotice({ error, retry }: { error: string | null; retry?: () => void }) {
  if (!error) return null
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-md border border-destructive/30 px-4 py-3 text-sm text-destructive"
    >
      <span>{error}</span>
      {retry && (
        <Button size="sm" variant="outline" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  )
}
function Pages({
  offset,
  next,
  change,
}: {
  offset: number
  next: number | null | undefined
  change: (value: number) => void
}) {
  if (!offset && next == null) return null
  return (
    <div className="flex justify-end gap-2 pt-4">
      <Button
        variant="outline"
        size="sm"
        disabled={!offset}
        onClick={() => change(Math.max(0, offset - 20))}
      >
        Previous
      </Button>
      <Button
        variant="outline"
        size="sm"
        disabled={next == null}
        onClick={() => next != null && change(next)}
      >
        Next
      </Button>
    </div>
  )
}
function JobRows({ jobs, open }: { jobs: PortfolioJob[]; open: (job: PortfolioJob) => void }) {
  return (
    <div className="divide-y">
      {jobs.map((job) => (
        <button
          key={job.id}
          type="button"
          onClick={() => open(job)}
          className="flex w-full items-center justify-between gap-4 py-4 text-left hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
        >
          <span className="min-w-0">
            <span className="block truncate font-medium">{label(job)}</span>
            <span className="mt-1 block text-xs text-muted-foreground">
              {job.kind === 'portfolio_optimize' ? 'Optimization' : 'Backtest'} ·{' '}
              {date(job.created_at)}
            </span>
          </span>
          <span className="shrink-0 text-xs text-muted-foreground">{researchRunStatus(job)}</span>
        </button>
      ))}
    </div>
  )
}
export default function ResearchLibrary() {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  const [params, setParams] = useSearchParams()
  const id = params.get('experiment')
  const job = params.get('job')
  const chartinkImport = params.get('chartink_import')
  if (chartinkImport !== null)
    return <ChartinkImport key={chartinkImport} requestId={chartinkImport} owner={owner} />
  if (id) return <ExperimentLoader key={`${owner}:${id}`} id={id} owner={owner} />
  if (job) return <PreviousReport key={`${owner}:${job}`} owner={owner} />
  return <LibraryIndex key={owner} owner={owner} params={params} setParams={setParams} />
}
function PreviousReport({ owner }: { owner: string }) {
  const [, setParams] = useSearchParams()
  const requests = useRef<{ key: string; id: string } | null>(null)
  async function reuseSetup(job: PortfolioJob, mode: 'backtest' | 'optimize', trialId?: string) {
    const key = `${job.id}:${mode}:${trialId ?? ''}`
    const request = requests.current?.key === key ? requests.current.id : crypto.randomUUID()
    requests.current = { key, id: request }
    const experiment = await researchLibrary.fromJob({
      job_id: job.id,
      mode,
      trial_id: trialId,
      request_id: request,
    })
    if ((useAuthStore.getState().user?.username ?? 'account') === owner)
      setParams({ experiment: experiment.id, view: 'setup' })
  }
  return (
    <>
      <div className="mx-auto max-w-6xl px-4 pt-5 sm:px-6">
        <Button variant="ghost" size="sm" onClick={() => setParams({ library: 'previous' })}>
          <ArrowLeft className="mr-2 size-4" />
          Research library
        </Button>
      </div>
      <PortfolioResearch onUseSetup={reuseSetup} />
    </>
  )
}
function LibraryIndex({
  owner,
  params,
  setParams,
}: {
  owner: string
  params: URLSearchParams
  setParams: ReturnType<typeof useSearchParams>[1]
}) {
  const queryClient = useQueryClient()
  const selection = params.get('library') ?? 'experiments'
  const tab = tabs.some(([id]) => id === selection) ? selection : 'experiments'
  const search = params.get('q') ?? ''
  const offset = nonnegative(params.get('offset'))
  const [busy, setBusy] = useState(false)
  const locked = useRef(false)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const filter = { search, archived: tab === 'archived', limit: 20, offset }
  const experiments = useQuery({
    queryKey: ['research-library', owner, tab, search, offset],
    queryFn: ({ signal }) => researchLibrary.list(filter, signal),
    enabled: tab === 'experiments' || tab === 'archived',
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => item.active_job_count) ? 5000 : false,
  })
  const studies = useQuery({
    queryKey: ['research-library-studies', owner, search, offset],
    queryFn: ({ signal }) => researchLibrary.studies(filter, signal),
    enabled: tab === 'studies',
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchInterval: (query) => (query.state.data?.items.some(active) ? 5000 : false),
  })
  const setups = useQuery({
    queryKey: ['research-library-versions', owner, search, offset],
    queryFn: ({ signal }) => researchLibrary.versions(filter, signal),
    enabled: tab === 'setups',
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
  })
  const sources = useQuery({
    queryKey: ['portfolio-sources', owner, offset],
    queryFn: ({ signal }) => portfolioResearch.sources(offset, signal),
    enabled: tab === 'sources',
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
  })
  const previous = useQuery({
    queryKey: ['portfolio-jobs', owner, params.get('cursor') ?? ''],
    queryFn: ({ signal }) =>
      portfolioResearch.jobs({ page_size: 50, cursor: params.get('cursor') ?? undefined }, signal),
    enabled: tab === 'previous',
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchInterval: (query) => (query.state.data?.items.some(active) ? 5000 : false),
  })
  function libraryParams(next: Record<string, string>) {
    setParams(next)
    setError(null)
  }
  function open(id: string, view = 'overview', job?: string, version?: string) {
    const next = new URLSearchParams(params)
    next.set('experiment', id)
    next.set('view', view)
    if (job) next.set('job', job)
    if (version) next.set('version', version)
    setParams(next)
  }
  async function action(fn: () => Promise<void>) {
    if (locked.current) return
    locked.current = true
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (cause) {
      setError(researchError(cause))
    } finally {
      locked.current = false
      setBusy(false)
    }
  }
  async function create() {
    await action(async () => {
      const experiment = await researchLibrary.create({
        name: name.trim() || 'Untitled experiment',
        draft: freshPortfolioDraft(),
      })
      queryClient.setQueryData(['research-experiment', owner, experiment.id], experiment)
      setCreating(false)
      open(experiment.id, 'setup')
    })
  }
  async function chooseSource(source: PortfolioSource) {
    await action(async () => {
      const draft = addPortfolioSource(freshPortfolioDraft(), source, sourceLabel(source))
      const experiment = await researchLibrary.create({
        name: draft.portfolio.strategies[0].name,
        draft,
      })
      open(experiment.id, 'setup')
    })
  }
  const selected =
    tab === 'studies'
      ? studies
      : tab === 'setups'
        ? setups
        : tab === 'sources'
          ? sources
          : tab === 'previous'
            ? previous
            : experiments
  return (
    <section className="mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Research library</h1>
        <Button
          onClick={() => {
            setCreating(true)
            setName('')
            setError(null)
          }}
          disabled={busy}
        >
          <Plus className="mr-2 size-4" />
          New experiment
        </Button>
      </header>
      <nav aria-label="Research library" className="flex gap-1 overflow-x-auto border-b pb-2">
        {tabs.map(([id, title]) => (
          <Button
            key={id}
            variant={tab === id ? 'secondary' : 'ghost'}
            size="sm"
            className="shrink-0"
            aria-current={tab === id ? 'page' : undefined}
            onClick={() => libraryParams(id === 'experiments' ? {} : { library: id })}
          >
            {title}
          </Button>
        ))}
      </nav>
      {!['sources', 'previous'].includes(tab) && (
        <div className="relative max-w-md">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground"
          />
          <Input
            aria-label="Search research"
            placeholder="Search names, notes or tags"
            value={search}
            className="pl-9"
            onChange={(event) => {
              const next = new URLSearchParams(params)
              next.set('q', event.target.value)
              next.delete('offset')
              setParams(next, { replace: true })
            }}
          />
        </div>
      )}
      <ErrorNotice
        error={error ?? (selected.error ? researchError(selected.error) : null)}
        retry={
          selected.error
            ? () => {
                void selected.refetch()
              }
            : undefined
        }
      />
      {selected.isLoading ? (
        <output className="block py-12 text-center text-sm text-muted-foreground">
          Loading research…
        </output>
      ) : (
        <>
          {(tab === 'experiments' || tab === 'archived') && (
            <div className="divide-y">
              {experiments.data?.items.map((item: ExperimentSummary) => (
                <button
                  type="button"
                  key={item.id}
                  onClick={() => open(item.id)}
                  className="flex w-full items-center justify-between gap-4 py-5 text-left hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-2 font-medium">
                      {item.pinned && <Pin aria-label="Pinned" className="size-3.5" />}
                      <span className="truncate">{item.name}</span>
                    </span>
                    <span className="mt-1 block break-words text-xs text-muted-foreground">
                      {count(item.job_count, 'run')} · {count(item.version_count, 'setup')}
                      <span className="sm:hidden"> · {date(item.updated_at)}</span>
                      {item.tags.length ? ` · ${item.tags.join(' · ')}` : ''}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-4 text-xs text-muted-foreground">
                    {!!item.active_job_count && (
                      <span className="text-primary">
                        {count(item.active_job_count, 'active run')}
                      </span>
                    )}
                    <span className="hidden sm:inline">{date(item.updated_at)}</span>
                    <ArrowUpRight className="size-4" />
                  </span>
                </button>
              ))}
              {experiments.data && !experiments.data.items.length && (
                <div className="py-14 text-center">
                  <h2 className="font-medium">
                    {search
                      ? 'No matching experiments'
                      : tab === 'archived'
                        ? 'No archived experiments'
                        : 'Start with a research question'}
                  </h2>
                  {!search && tab !== 'archived' && (
                    <p className="mt-2 text-sm text-muted-foreground">
                      Keep your setups, backtests and studies together.
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
          {tab === 'studies' && (
            <div className="divide-y">
              {studies.data?.items.map((item: LibraryJob) => (
                <button
                  key={`${item.experiment_id}:${item.id}`}
                  type="button"
                  onClick={() => open(item.experiment_id, 'studies', item.id)}
                  className="flex w-full items-center justify-between gap-4 py-4 text-left hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{label(item)}</span>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {item.experiment_name} · {date(item.created_at)}
                    </span>
                  </span>
                  <span className="text-xs capitalize text-muted-foreground">{item.status}</span>
                </button>
              ))}
              {studies.data && !studies.data.items.length && (
                <p className="py-12 text-center text-sm text-muted-foreground">
                  No studies yet. Optimize a setup in an experiment.
                </p>
              )}
            </div>
          )}
          {tab === 'setups' && (
            <div className="divide-y">
              {setups.data?.items.map((item: SetupVersionSummary) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => open(item.experiment_id, 'setups', undefined, item.id)}
                  className="flex w-full items-center justify-between gap-4 py-4 text-left hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{item.name}</span>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {item.experiment_name} · Version {item.number}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {date(item.created_at)}
                  </span>
                </button>
              ))}
              {setups.data && !setups.data.items.length && (
                <p className="py-12 text-center text-sm text-muted-foreground">
                  No saved setups yet.
                </p>
              )}
            </div>
          )}
          {tab === 'sources' && (
            <div className="divide-y">
              {sources.data?.items.map((item) => (
                <div key={item.id} className="flex items-center justify-between gap-4 py-4">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{sourceLabel(item)}</p>
                    <ChartinkSource source={item.receipt.chartink} />
                    <p className="mt-1 text-xs text-muted-foreground">
                      {item.receipt.date_from} – {item.receipt.date_to} ·{' '}
                      {count(item.receipt.signal_count, 'signal')}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      void chooseSource(item)
                    }}
                  >
                    Use signals
                  </Button>
                </div>
              ))}
              {sources.data && !sources.data.items.length && (
                <p className="py-12 text-center text-sm text-muted-foreground">
                  Uploaded signal files appear here.
                </p>
              )}
            </div>
          )}
          {tab === 'previous' && (
            <>
              <JobRows
                jobs={previous.data?.items ?? []}
                open={(job) => {
                  const next = new URLSearchParams(params)
                  next.set('job', job.id)
                  if (!['portfolio_backtest', 'portfolio_optimize'].includes(job.kind ?? ''))
                    next.set('legacy', '1')
                  setParams(next)
                }}
              />
              {previous.data && !previous.data.items.length && (
                <p className="py-12 text-center text-sm text-muted-foreground">No previous runs.</p>
              )}
              {(params.get('cursor') || previous.data?.next_cursor) && (
                <div className="flex justify-end gap-2">
                  <Button
                    variant="outline"
                    disabled={!params.get('cursor')}
                    onClick={() => libraryParams({ library: 'previous' })}
                  >
                    Newest runs
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!previous.data?.next_cursor}
                    onClick={() =>
                      previous.data?.next_cursor &&
                      libraryParams({ library: 'previous', cursor: previous.data.next_cursor })
                    }
                  >
                    More runs
                  </Button>
                </div>
              )}
            </>
          )}
          {tab !== 'previous' && (
            <Pages
              offset={offset}
              next={
                tab === 'studies'
                  ? studies.data?.next_offset
                  : tab === 'setups'
                    ? setups.data?.next_offset
                    : tab === 'sources'
                      ? sources.data?.next_offset
                      : experiments.data?.next_offset
              }
              change={(value) => {
                const next = new URLSearchParams(params)
                next.set('offset', String(value))
                setParams(next)
              }}
            />
          )}
        </>
      )}
      <Dialog open={creating} onOpenChange={(open) => !busy && setCreating(open)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New experiment</DialogTitle>
            <DialogDescription className="sr-only">
              Name the idea you want to research.
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              void create()
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="experiment-name">Research question or name</Label>
              <Input
                id="experiment-name"
                value={name}
                maxLength={120}
                placeholder="For example, breakout entries"
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            {error && <ErrorNotice error={error} />}
            <Button type="submit" disabled={busy}>
              {busy ? 'Creating…' : 'Create experiment'}
            </Button>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  )
}
function ExperimentLoader({ id, owner }: { id: string; owner: string }) {
  const [params, setParams] = useSearchParams()
  const first = useRef<ResearchExperiment | null>(null)
  const data = useQuery({
    queryKey: ['research-experiment', owner, id],
    queryFn: ({ signal }) => researchLibrary.get(id, signal),
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: false,
  })
  if (data.isPending || (!first.current && data.isFetching))
    return (
      <section className="mx-auto max-w-6xl px-4 py-12">
        <output>Opening experiment…</output>
      </section>
    )
  if (!data.data || (!first.current && data.error))
    return (
      <section className="mx-auto max-w-6xl space-y-4 px-4 py-8">
        <Button
          variant="ghost"
          onClick={() => {
            const next = new URLSearchParams(params)
            next.delete('experiment')
            next.delete('job')
            next.delete('view')
            setParams(next)
          }}
        >
          Research library
        </Button>
        <ErrorNotice
          error={researchError(data.error)}
          retry={() => {
            void data.refetch()
          }}
        />
      </section>
    )
  first.current ??= data.data
  return <ExperimentWorkspace key={id} initial={first.current} owner={owner} />
}
function ExperimentWorkspace({ initial, owner }: { initial: ResearchExperiment; owner: string }) {
  const model = useResearchExperiment(initial, owner)
  const { server, draft } = model
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const jobId = params.get('job')
  const requestedView = params.get('view') ?? 'overview'
  const view = [
    'overview',
    'setup',
    'backtests',
    'studies',
    'setups',
    'shortlist',
    'comparisons',
  ].includes(requestedView)
    ? requestedView
    : 'overview'
  const experimentNav = useRef<HTMLElement | null>(null)
  useEffect(() => {
    const nav = experimentNav.current
    if (!nav || jobId) return
    const revealActive = () => {
      const selected = nav.querySelector<HTMLElement>(
        `[data-experiment-view="${view}"][aria-current="page"]`
      )
      if (!selected) return
      const frame = nav.getBoundingClientRect()
      const button = selected.getBoundingClientRect()
      if (button.left < frame.left) nav.scrollLeft += button.left - frame.left
      else if (button.right > frame.right) nav.scrollLeft += button.right - frame.right
    }
    revealActive()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(revealActive)
    observer.observe(nav)
    return () => observer.disconnect()
  }, [view, jobId])
  const activeJobIds = server.jobs
    .filter(active)
    .map((job) => job.id)
    .sort()
  const liveJobs = useQuery({
    queryKey: ['research-experiment-activity', owner, server.id, activeJobIds],
    queryFn: ({ signal }) =>
      Promise.all(activeJobIds.map((id) => portfolioResearch.job(id, signal))),
    enabled: !jobId && view !== 'comparisons' && activeJobIds.length > 0,
    refetchInterval: 3000,
    staleTime: 0,
    retry: false,
  })
  useEffect(() => {
    for (const job of liveJobs.data ?? []) model.reflectJob(job)
  }, [liveJobs.data, model.reflectJob])
  const [busy, setBusy] = useState(false)
  const locked = useRef(false)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [editNotes, setEditNotes] = useState('')
  const [editTags, setEditTags] = useState('')
  const [savingVersion, setSavingVersion] = useState(false)
  const [versionName, setVersionName] = useState('')
  const [deleting, setDeleting] = useState(false)
  const runRequest = useRef<{ revision: number; id: string } | null>(null)
  const copyRequests = useRef<{ key: string; id: string } | null>(null)
  const dirty = model.state !== 'saved'
  useEffect(() => {
    if (jobId || !['overview', 'backtests', 'studies'].includes(view)) return
    void model.refreshJobs().catch((cause: unknown) => {
      if ((useAuthStore.getState().user?.username ?? 'account') === owner)
        setError(researchError(cause))
    })
  }, [jobId, view, owner, model.refreshJobs])
  function changeView(nextView: string, job?: string) {
    const next = new URLSearchParams(params)
    next.set('view', nextView)
    next.delete('job')
    next.delete('version')
    next.delete('return_job')
    next.delete('report')
    next.delete('return_shortlist')
    next.delete('shortlist')
    next.delete('comparison')
    next.delete('comparison_member')
    if (job) next.set('job', job)
    setParams(next)
  }
  function library() {
    const next = new URLSearchParams(params)
    for (const key of [
      'experiment',
      'job',
      'view',
      'version',
      'return_job',
      'report',
      'shortlist',
      'shortlist_offset',
      'return_shortlist',
      'comparison',
      'comparison_member',
      'comparison_offset',
      'compare_candidates',
      'compare_reference',
      'compare_request',
    ])
      next.delete(key)
    setParams(next)
  }
  async function operation<T>(fn: () => Promise<T>): Promise<T> {
    if (locked.current) throw new Error('Wait for the current action to finish.')
    locked.current = true
    setBusy(true)
    try {
      return await fn()
    } finally {
      locked.current = false
      setBusy(false)
    }
  }
  async function action(fn: () => Promise<void>) {
    if (locked.current) return
    locked.current = true
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (cause) {
      model.reject(cause)
      setError(researchError(cause))
    } finally {
      locked.current = false
      setBusy(false)
    }
  }
  async function update(
    patch: Partial<Pick<ExperimentSummary, 'name' | 'notes' | 'tags' | 'pinned' | 'archived'>>
  ) {
    const latest = await model.flush()
    const next = await researchLibrary.update(server.id, { revision: latest.revision, ...patch })
    model.accept(next)
    await queryClient.invalidateQueries({ queryKey: ['research-library', owner] })
  }
  async function run() {
    const latest = await model.flush()
    if (runRequest.current?.revision !== latest.revision)
      runRequest.current = { revision: latest.revision, id: crypto.randomUUID() }
    const result = await researchLibrary
      .run(server.id, latest.revision, runRequest.current.id)
      .catch((cause: unknown) => {
        model.reject(cause)
        throw cause
      })
    model.accept(result.experiment)
    runRequest.current = null
    return result.job
  }
  async function reuseSetup(job: PortfolioJob, mode: 'backtest' | 'optimize', trialId?: string) {
    const latest = await model.flush()
    const key = `${job.id}:${trialId ?? ''}:${mode}:${latest.revision}`
    const request =
      copyRequests.current?.key === key ? copyRequests.current.id : crypto.randomUUID()
    copyRequests.current = { key, id: request }
    const next = await researchLibrary
      .fromJob({
        job_id: job.id,
        trial_id: trialId,
        mode,
        request_id: request,
        experiment_id: server.id,
        revision: latest.revision,
      })
      .catch((cause: unknown) => {
        model.reject(cause)
        throw cause
      })
    model.accept(next)
    changeView('setup')
  }
  async function replay(origin: string, trialId?: string, period?: 'selection' | 'evaluation') {
    const latest = await model.flush()
    const key = `replay:${origin}:${trialId ?? ''}:${period ?? 'selection'}:${latest.revision}`
    const request =
      copyRequests.current?.key === key ? copyRequests.current.id : crypto.randomUUID()
    copyRequests.current = { key, id: request }
    const result = await researchLibrary
      .replay(server.id, latest.revision, origin, request, trialId, period)
      .catch((cause: unknown) => {
        model.reject(cause)
        throw cause
      })
    model.accept(result.experiment)
    return result.job
  }
  async function forkDraft() {
    const next = await researchLibrary.create({
      name: `${server.name.slice(0, 110)} (copy)`,
      notes: server.notes,
      tags: server.tags,
      draft,
    })
    await model.reload()
    const url = new URLSearchParams(params)
    url.set('experiment', next.id)
    url.set('view', 'setup')
    url.delete('job')
    setParams(url)
  }
  async function restore(version: string) {
    const latest = await model.flush()
    model.accept(await researchLibrary.restoreVersion(server.id, version, latest.revision))
    changeView('setup')
  }
  const jobs =
    view === 'studies'
      ? server.jobs.filter((job) => job.kind === 'portfolio_optimize')
      : view === 'backtests'
        ? server.jobs.filter((job) => job.kind === 'portfolio_backtest')
        : server.jobs
  const inProgress = server.jobs.filter(active)
  const selectedVersion = params.get('version')
  const versionQuery = useQuery({
    queryKey: ['research-setup-version', owner, server.id, selectedVersion],
    queryFn: ({ signal }) => researchLibrary.getVersion(server.id, selectedVersion!, signal),
    enabled: Boolean(selectedVersion),
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
  })
  const displayedVersions =
    selectedVersion && versionQuery.data ? [versionQuery.data] : server.versions
  return (
    <section
      className={
        jobId
          ? 'mx-auto max-w-6xl space-y-3 px-4 py-3 sm:px-6'
          : 'mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6 sm:py-8'
      }
    >
      <header className={jobId ? 'space-y-2' : 'space-y-4'}>
        <Button
          variant="ghost"
          size="sm"
          className="-ml-3"
          disabled={busy}
          onClick={() => {
            void action(async () => {
              await model.flush()
              library()
            })
          }}
        >
          <ArrowLeft className="mr-2 size-4" />
          Research library
        </Button>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="break-words text-2xl font-semibold tracking-tight">{server.name}</h1>
            <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
              {server.archived ? (
                <span>Archived</span>
              ) : (
                <output aria-live="polite">
                  {model.state === 'saved'
                    ? 'Saved'
                    : model.state === 'saving'
                      ? 'Saving…'
                      : model.state === 'unsaved'
                        ? 'Unsaved changes'
                        : 'Not saved'}
                </output>
              )}
              {server.tags.length > 0 && <span>{server.tags.join(' · ')}</span>}
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            {view === 'setup' && !jobId && !server.archived && (
              <Button
                variant="outline"
                disabled={busy || model.state === 'conflict'}
                onClick={() => {
                  setVersionName(draft.portfolio.name)
                  setSavingVersion(true)
                }}
              >
                Save setup
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-label="Experiment details"
              disabled={busy}
              onClick={() => {
                setEditName(server.name)
                setEditNotes(server.notes)
                setEditTags(server.tags.join(', '))
                setEditing(true)
              }}
            >
              <MoreHorizontal className="size-5" />
            </Button>
          </div>
        </div>
      </header>
      <ErrorNotice error={error} />
      {(model.state === 'error' || model.state === 'conflict') && (
        <div className="space-y-3 rounded-md border border-destructive/30 p-4">
          <p role="alert" className="text-sm">
            {model.state === 'conflict'
              ? 'This experiment was changed in another tab. Keep your draft as a copy, or reload the saved draft.'
              : model.error}
          </p>
          <div className="flex flex-wrap gap-2">
            {model.state === 'conflict' ? (
              <>
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    void action(forkDraft)
                  }}
                >
                  Keep my draft as a copy
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    void action(model.reload)
                  }}
                >
                  Reload saved draft
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  void action(async () => {
                    await model.flush()
                  })
                }}
              >
                Retry save
              </Button>
            )}
          </div>
        </div>
      )}
      {server.archived && (
        <div className="flex items-center justify-between gap-4 border-y py-4 text-sm">
          <span>Saved results remain available.</span>
          <Button
            size="sm"
            onClick={() => {
              void action(() => update({ archived: false }))
            }}
            disabled={busy}
          >
            Restore experiment
          </Button>
        </div>
      )}
      <nav
        ref={experimentNav}
        aria-label="Experiment"
        className="flex gap-1 overflow-x-auto border-b pb-2"
      >
        {(
          [
            'overview',
            'setup',
            'backtests',
            'studies',
            'shortlist',
            'comparisons',
            'setups',
          ] as const
        ).map((item) => (
          <Button
            key={item}
            variant={!jobId && view === item ? 'secondary' : 'ghost'}
            size="sm"
            className="shrink-0"
            aria-current={!jobId && view === item ? 'page' : undefined}
            data-experiment-view={item}
            disabled={busy}
            onClick={() => {
              void action(async () => {
                await model.flush()
                changeView(item)
              })
            }}
          >
            {item === 'setups' ? 'Saved setups' : item.charAt(0).toUpperCase() + item.slice(1)}
          </Button>
        ))}
      </nav>
      {jobId && (
        <div className="flex items-center justify-between gap-3">
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() =>
              params.get('return_shortlist')
                ? (() => {
                    const next = new URLSearchParams(params)
                    next.set('view', 'shortlist')
                    next.set('shortlist', params.get('return_shortlist')!)
                    next.delete('return_shortlist')
                    next.delete('job')
                    next.delete('return_job')
                    next.delete('report')
                    setParams(next)
                  })()
                : params.get('return_job')
                  ? changeView('studies', params.get('return_job')!)
                  : changeView(view === 'studies' ? 'studies' : 'backtests')
            }
          >
            <ArrowLeft className="mr-2 size-4" />
            {params.get('return_shortlist')
              ? 'Back to shortlist'
              : params.get('return_job')
                ? 'Back to study'
                : `Back to ${view === 'studies' ? 'studies' : 'backtests'}`}
          </Button>
        </div>
      )}
      {(jobId || view === 'setup') && (
        <fieldset
          disabled={!jobId && (server.archived || busy || model.state === 'conflict')}
          className="min-w-0 border-0 p-0"
        >
          <PortfolioResearch
            key={jobId ?? 'draft'}
            workspace={{
              experimentId: server.id,
              draft,
              onChange: model.change,
              onRun: () => operation(run),
              onReplay: (origin, trialId, period) =>
                operation(() => replay(origin, trialId, period)),
              onJobUpdate: model.reflectJob,
              onOpenJob: (job, returnStudyId) => {
                if (params.get('return_shortlist')) {
                  const next = new URLSearchParams(params)
                  next.set('job', job.id)
                  next.set('view', job.kind === 'portfolio_optimize' ? 'studies' : 'backtests')
                  next.delete('report')
                  setParams(next)
                  return
                }
                if (
                  (returnStudyId || (view === 'studies' && jobId)) &&
                  job.id !== (returnStudyId || jobId) &&
                  job.kind !== 'portfolio_optimize'
                ) {
                  const next = new URLSearchParams(params)
                  next.set('job', job.id)
                  next.set('view', 'backtests')
                  next.set('return_job', returnStudyId || jobId!)
                  next.delete('report')
                  setParams(next)
                  void model.refreshJobs().catch((cause: unknown) => {
                    if ((useAuthStore.getState().user?.username ?? 'account') === owner)
                      setError(researchError(cause))
                  })
                } else
                  changeView(job.kind === 'portfolio_optimize' ? 'studies' : 'backtests', job.id)
              },
              disabled: busy || server.archived || model.state === 'conflict',
              readOnly: server.archived || model.state === 'conflict',
            }}
            onUseSetup={(job, mode, trialId) => operation(() => reuseSetup(job, mode, trialId))}
          />
        </fieldset>
      )}
      {!jobId && view === 'shortlist' && (
        <ResearchShortlist
          experimentId={server.id}
          readOnly={server.archived}
          onOpenComparison={(id) => {
            const next = new URLSearchParams(params)
            next.set('view', 'comparisons')
            next.set('comparison', id)
            next.delete('comparison_member')
            next.delete('shortlist')
            setParams(next)
          }}
          onOpenReport={(reportId, candidate) => {
            const next = new URLSearchParams(params)
            next.set('job', reportId)
            next.set('return_shortlist', candidate.id)
            next.set(
              'view',
              candidate.origin_kind === 'study' && reportId === candidate.source_job_id
                ? 'studies'
                : 'backtests'
            )
            next.delete('shortlist')
            next.delete('return_job')
            if (candidate.origin_kind === 'study' && reportId === candidate.source_job_id)
              next.set('report', 'best')
            else next.delete('report')
            setParams(next)
          }}
        />
      )}
      {!jobId && view === 'comparisons' && (
        <ResearchComparisons experimentId={server.id} readOnly={server.archived} />
      )}
      {!jobId && view === 'overview' && (
        <div className="space-y-7">
          {server.notes && (
            <p className="max-w-3xl whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
              {server.notes}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-4 border-b pb-6">
            <div>
              <h2 className="font-medium">{draft.portfolio.name}</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {count(draft.portfolio.strategies.length, 'strategy')} ·{' '}
                {count(server.version_count, 'saved setup')}
              </p>
            </div>
            <Button disabled={busy || server.archived} onClick={() => changeView('setup')}>
              {draft.portfolio.strategies.length ? 'Continue setup' : 'Add signals'}
            </Button>
          </div>
          {inProgress.length > 0 && (
            <section>
              <h2 className="text-sm font-medium">In progress</h2>
              <JobRows
                jobs={inProgress}
                open={(job) =>
                  changeView(job.kind === 'portfolio_optimize' ? 'studies' : 'backtests', job.id)
                }
              />
            </section>
          )}
          <section>
            <h2 className="text-sm font-medium">Recent results</h2>
            <JobRows
              jobs={server.jobs.filter((job) => !active(job)).slice(0, 5)}
              open={(job) =>
                changeView(job.kind === 'portfolio_optimize' ? 'studies' : 'backtests', job.id)
              }
            />
            {!server.jobs.length && (
              <p className="py-6 text-sm text-muted-foreground">
                Your backtests and optimization studies will appear here.
              </p>
            )}
          </section>
        </div>
      )}
      {!jobId && (view === 'backtests' || view === 'studies') && (
        <>
          <JobRows jobs={jobs} open={(job) => changeView(view, job.id)} />
          {!jobs.length && (
            <p className="py-10 text-center text-sm text-muted-foreground">
              No {view} in this experiment yet.
            </p>
          )}
          {server.jobs_next_offset != null && (
            <Button
              variant="outline"
              onClick={() => {
                void action(async () => {
                  const latest = await model.flush()
                  const page = await researchLibrary.get(server.id, undefined, {
                    jobs_offset: latest.jobs_next_offset ?? 0,
                  })
                  model.accept({ ...page, jobs: [...latest.jobs, ...page.jobs] })
                })
              }}
            >
              More runs
            </Button>
          )}
        </>
      )}
      {!jobId && view === 'setups' && (
        <div className="divide-y">
          <ErrorNotice error={versionQuery.error ? researchError(versionQuery.error) : null} />
          {displayedVersions.map((version) => (
            <div
              key={version.id}
              className="flex flex-wrap items-center justify-between gap-4 py-4"
            >
              <div>
                <h2 className="font-medium">{version.name}</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  Version {version.number} · {date(version.created_at)} ·{' '}
                  {count(version.draft.portfolio.strategies.length, 'strategy')}
                </p>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={busy || server.archived}
                onClick={() => {
                  void action(() => restore(version.id))
                }}
              >
                Use setup
              </Button>
            </div>
          ))}
          {!server.versions.length && (
            <p className="py-10 text-center text-sm text-muted-foreground">
              Save a setup or run a backtest to keep a version.
            </p>
          )}
          {!selectedVersion && server.versions_next_offset != null && (
            <Button
              variant="outline"
              onClick={() => {
                void action(async () => {
                  const latest = await model.flush()
                  const page = await researchLibrary.get(server.id, undefined, {
                    versions_offset: latest.versions_next_offset ?? 0,
                  })
                  model.accept({ ...page, versions: [...latest.versions, ...page.versions] })
                })
              }}
            >
              More setups
            </Button>
          )}
        </div>
      )}
      <Dialog open={editing} onOpenChange={(open) => !busy && setEditing(open)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Experiment details</DialogTitle>
            <DialogDescription className="sr-only">
              Name and organize this research question.
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              void action(async () => {
                await update({
                  name: editName,
                  notes: editNotes,
                  tags: editTags
                    .split(',')
                    .map((tag) => tag.trim())
                    .filter(Boolean),
                })
                setEditing(false)
              })
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="research-title">Name</Label>
              <Input
                id="research-title"
                value={editName}
                maxLength={120}
                onChange={(event) => setEditName(event.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="research-notes">Research notes</Label>
              <Textarea
                id="research-notes"
                value={editNotes}
                maxLength={10000}
                onChange={(event) => setEditNotes(event.target.value)}
                rows={4}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="research-tags">Tags</Label>
              <Input
                id="research-tags"
                value={editTags}
                onChange={(event) => setEditTags(event.target.value)}
                placeholder="Comma-separated tags"
              />
            </div>
            {error && <ErrorNotice error={error} />}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Button type="submit" disabled={busy || !editName.trim()}>
                Save details
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  void action(async () => {
                    await update({ pinned: !server.pinned })
                    setEditing(false)
                  })
                }}
              >
                <Pin className="mr-2 size-4" />
                {server.pinned ? 'Unpin' : 'Pin'}
              </Button>
            </div>
            <div className="flex flex-wrap justify-between gap-2 border-t pt-3">
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  void action(async () => {
                    await update({ archived: !server.archived })
                    setEditing(false)
                  })
                }}
              >
                <Archive className="mr-2 size-4" />
                {server.archived ? 'Restore' : 'Archive'}
              </Button>
              {!server.job_count && !server.version_count && !server.parent_job_id && (
                <Button
                  type="button"
                  variant="ghost"
                  className="text-destructive"
                  disabled={busy}
                  onClick={() => {
                    setEditing(false)
                    setDeleting(true)
                  }}
                >
                  Delete draft
                </Button>
              )}
            </div>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={savingVersion} onOpenChange={(open) => !busy && setSavingVersion(open)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Save setup</DialogTitle>
            <DialogDescription className="sr-only">
              Save these settings so you can use them again.
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              void action(async () => {
                const latest = await model.flush()
                const saved = await researchLibrary.saveVersion(
                  server.id,
                  latest.revision,
                  versionName
                )
                model.accept(saved.experiment)
                setSavingVersion(false)
                changeView('setups')
              })
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="setup-version-name">Setup name</Label>
              <Input
                id="setup-version-name"
                value={versionName}
                maxLength={120}
                required
                onChange={(event) => setVersionName(event.target.value)}
              />
            </div>
            {error && <ErrorNotice error={error} />}
            <Button disabled={busy || !versionName.trim()} type="submit">
              Save version
            </Button>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={deleting} onOpenChange={(open) => !busy && setDeleting(open)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this draft?</DialogTitle>
            <DialogDescription>
              This removes the experiment and its unfinished draft. Uploaded signal files remain
              available.
            </DialogDescription>
          </DialogHeader>
          {error && <ErrorNotice error={error} />}
          <div className="flex justify-end gap-2">
            <Button variant="outline" disabled={busy} onClick={() => setDeleting(false)}>
              Keep draft
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => {
                void action(async () => {
                  const latest = await model.flush()
                  await researchLibrary.remove(server.id, latest.revision)
                  library()
                })
              }}
            >
              Delete draft
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      {dirty && model.state === 'unsaved' && (
        <span className="sr-only">Your latest changes are waiting to save.</span>
      )}
    </section>
  )
}
