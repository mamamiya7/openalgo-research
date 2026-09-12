import { ArrowDown, ArrowUp, ArrowUpDown, Columns3 } from 'lucide-react'
import { type ReactNode, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { PortfolioResult, PortfolioSettings } from '@/api/portfolioResearch'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuthStore } from '@/stores/authStore'
import { AnalysisMetricTable } from './AnalysisMetricTable'
import {
  availableTrialMetrics,
  defaultTrialColumns,
  readTrialColumns,
  trialMetricText,
  trialMetricValue,
  writeTrialColumns,
} from './portfolioTrialMetrics'
import {
  defaultStudyTrialView,
  type StudyTrialRow,
  type StudyTrialView,
  sortStudyTrials,
  studyTrialPage,
  studyTrialRows,
} from './studyTrialView'

interface Props {
  experiment: NonNullable<PortfolioResult['experiment']>
  page: number
  onPageChange: (page: number) => void
  onRerun: (trialId: string) => void
  rerunning: boolean
  readOnly: boolean
  renderSettings: (strategy: PortfolioSettings) => ReactNode
  view?: StudyTrialView
  onViewChange?: (view: StudyTrialView) => void
  onInspect?: (configId: string, proposalNumber?: number) => void
  onReport?: (configId: string) => void
  reportAction?: (configId: string) => { label: string; disabled?: boolean }
}
const cell = 'whitespace-nowrap px-3 py-3 text-left text-sm tabular-nums'
const heading = `${cell} sticky top-0 z-20 bg-muted text-xs font-medium text-muted-foreground`
const pageSize = 25

export function PortfolioTrials(props: Props) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  return <TrialTable key={owner} {...props} owner={owner} />
}

function TrialTable({
  experiment,
  page: requestedPage,
  onPageChange: setPage,
  onRerun,
  rerunning,
  readOnly,
  renderSettings,
  view: controlledView,
  onViewChange,
  onInspect,
  onReport,
  reportAction,
  owner,
}: Props & { owner: string }) {
  const [detail, setDetail] = useState<string | null>(null)
  const [detailNumber, setDetailNumber] = useState<number | null>(null)
  const [localView, setLocalView] = useState(defaultStudyTrialView)
  const tableRegion = useRef<HTMLElement | null>(null)
  const view = controlledView ?? { ...localView, page: requestedPage }
  const updateView = (changes: Partial<StudyTrialView>) => {
    const next = { ...view, ...changes }
    if (
      tableRegion.current &&
      (next.page !== view.page ||
        next.scope !== view.scope ||
        next.sortKey !== view.sortKey ||
        next.sortDirection !== view.sortDirection)
    ) {
      tableRegion.current.scrollTop = 0
      next.scrollTop = 0
    }
    if (onViewChange) onViewChange(next)
    else {
      setLocalView(next)
      if (next.page !== requestedPage) setPage(next.page)
    }
  }
  useLayoutEffect(() => {
    const region = tableRegion.current
    if (!region) return
    region.scrollTop = view.scrollTop ?? 0
    region.scrollLeft = view.scrollLeft ?? 0
  }, [view.scrollTop, view.scrollLeft])
  const settingsTrigger = useRef<HTMLButtonElement | null>(null)
  const settingsTitle = useRef<HTMLHeadingElement | null>(null)
  const [columns, setColumns] = useState(() => readTrialColumns(owner, experiment.analysis_catalog))
  const [columnsOpen, setColumnsOpen] = useState(false)
  const [draft, setDraft] = useState<string[]>([])
  const [metricSearch, setMetricSearch] = useState('')
  const available = useMemo(
    () => availableTrialMetrics(experiment.rows, experiment.analysis_catalog),
    [experiment.rows, experiment.analysis_catalog]
  )
  const groups = [...new Set(available.map((metric) => metric.group))]
  const defaults = available.filter((metric) => defaultTrialColumns.includes(metric.key))
  const requested = available.filter((metric) => columns.includes(metric.key))
  const visible = requested.length ? requested : defaults.length ? defaults : available.slice(0, 1)
  const rows = useMemo(
    () =>
      sortStudyTrials(studyTrialRows(experiment, view.scope), experiment, {
        sortKey: view.sortKey,
        sortDirection: view.sortDirection,
      }),
    [experiment, view.scope, view.sortKey, view.sortDirection]
  )
  const { page, rows: pageRows } = studyTrialPage(rows, view.page, pageSize)
  const trial = experiment.rows.find((row) => row.config_id === detail)
  const distinctCount = new Set(experiment.rows.map((row) => row.config_id)).size
  const reusedCount = experiment.trials
    ? experiment.trials.filter((proposal) => proposal.reused).length
    : experiment.counts.reused_trials
  const rejectedCount = experiment.trials
    ? experiment.trials.filter((proposal) => proposal.state === 'pruned').length
    : experiment.counts.rejected_allocations
  const inspect = (row: StudyTrialRow, trigger: HTMLButtonElement) => {
    updateView({ selectedConfigId: row.configId })
    if (onInspect) onInspect(row.configId, row.number)
    else {
      settingsTrigger.current = trigger
      setDetail(row.configId)
      setDetailNumber(row.number)
    }
  }
  const sort = (key: string) =>
    updateView({
      sortKey: key,
      sortDirection:
        view.sortKey === key
          ? view.sortDirection === 'asc'
            ? 'desc'
            : 'asc'
          : key === 'trial_number'
            ? 'asc'
            : 'desc',
      page: 0,
    })
  const sortState = (key: string) =>
    view.sortKey === key
      ? view.sortDirection === 'asc'
        ? ('ascending' as const)
        : ('descending' as const)
      : ('none' as const)
  const SortIcon = ({ metricKey }: { metricKey: string }) => {
    const Icon =
      view.sortKey === metricKey
        ? view.sortDirection === 'asc'
          ? ArrowUp
          : ArrowDown
        : ArrowUpDown
    return <Icon className="size-3 shrink-0" aria-hidden="true" />
  }
  const restoreDefaults = () =>
    setDraft((defaults.length ? defaults : available.slice(0, 1)).map((metric) => metric.key))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {distinctCount} distinct portfolios
          {rejectedCount > 0 ? ` · ${rejectedCount} allocation combinations excluded` : ''}
          {reusedCount > 0 ? ` · ${reusedCount} repeated proposals reused` : ''}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <fieldset className="flex rounded-md border p-0.5" aria-label="Trial scope">
            <Button
              type="button"
              variant={view.scope === 'distinct' ? 'secondary' : 'ghost'}
              size="sm"
              aria-pressed={view.scope === 'distinct'}
              onClick={() => updateView({ scope: 'distinct', page: 0 })}
            >
              Distinct portfolios
            </Button>
            <Button
              type="button"
              variant={view.scope === 'all' ? 'secondary' : 'ghost'}
              size="sm"
              aria-pressed={view.scope === 'all'}
              onClick={() => updateView({ scope: 'all', page: 0 })}
            >
              All proposals
            </Button>
          </fieldset>
          <Dialog
            open={columnsOpen}
            onOpenChange={(open) => {
              if (open) {
                setDraft(visible.map((metric) => metric.key))
                setMetricSearch('')
              }
              setColumnsOpen(open)
            }}
          >
            <DialogTrigger asChild>
              <Button type="button" variant="outline" size="sm" disabled={!available.length}>
                <Columns3 className="mr-2 size-4" aria-hidden="true" />
                Columns
              </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[85dvh] overflow-y-auto motion-reduce:animate-none">
              <DialogHeader>
                <DialogTitle>Table columns</DialogTitle>
                <DialogDescription>
                  Choose what to show. Your selection is remembered on this browser.
                </DialogDescription>
              </DialogHeader>
              {available.length > 20 && (
                <Input
                  aria-label="Find a table column"
                  placeholder="Find a statistic…"
                  value={metricSearch}
                  onChange={(event) => setMetricSearch(event.target.value)}
                />
              )}
              <div className="space-y-5">
                {groups.map((group) => {
                  const choices = available.filter(
                    (metric) =>
                      metric.group === group &&
                      `${metric.label} ${metric.source ?? ''} ${metric.description}`
                        .toLowerCase()
                        .includes(metricSearch.trim().toLowerCase())
                  )
                  if (!choices.length) return null
                  return (
                    <fieldset key={group} className="space-y-3">
                      <legend className="mb-3 text-xs font-medium text-muted-foreground">
                        {group}
                      </legend>
                      <div className="grid gap-3 sm:grid-cols-2">
                        {choices.map((metric) => (
                          <label
                            key={metric.key}
                            className="flex items-center gap-3 text-sm"
                            title={metric.description}
                          >
                            <Checkbox
                              checked={draft.includes(metric.key)}
                              disabled={draft.length === 1 && draft.includes(metric.key)}
                              onCheckedChange={(checked) =>
                                setDraft((current) =>
                                  checked
                                    ? [...current.filter((key) => key !== metric.key), metric.key]
                                    : current.filter((key) => key !== metric.key)
                                )
                              }
                            />
                            {metric.label}
                          </label>
                        ))}
                      </div>
                    </fieldset>
                  )
                })}
              </div>
              <DialogFooter className="mt-2 gap-2 sm:justify-between">
                <Button type="button" variant="ghost" onClick={restoreDefaults}>
                  Reset to default
                </Button>
                <div className="flex justify-end gap-2">
                  <DialogClose asChild>
                    <Button type="button" variant="outline">
                      Cancel
                    </Button>
                  </DialogClose>
                  <Button
                    type="button"
                    disabled={!draft.length}
                    onClick={() => {
                      const next = available
                        .filter((metric) => draft.includes(metric.key))
                        .map((metric) => metric.key)
                      setColumns(next)
                      writeTrialColumns(owner, next)
                      setColumnsOpen(false)
                    }}
                  >
                    Apply
                  </Button>
                </div>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      <section
        ref={tableRegion}
        onScroll={(event) => {
          const region = event.currentTarget
          if (region.scrollTop !== view.scrollTop || region.scrollLeft !== view.scrollLeft)
            updateView({ scrollTop: region.scrollTop, scrollLeft: region.scrollLeft })
        }}
        className="max-h-[60vh] overflow-auto rounded-lg border"
        aria-label="Trial comparison"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users must be able to scroll this bounded comparison table.
        tabIndex={0}
      >
        <table className="w-full">
          <caption className="sr-only">Optimization trials</caption>
          <thead>
            <tr>
              <th
                scope="col"
                className={`${heading} left-0 z-30`}
                aria-sort={sortState('trial_number')}
              >
                <button
                  type="button"
                  className="inline-flex items-center gap-2"
                  onClick={() => sort('trial_number')}
                >
                  Trial <SortIcon metricKey="trial_number" />
                </button>
              </th>
              {view.scope === 'all' && (
                <th scope="col" className={heading}>
                  Status
                </th>
              )}
              {visible.map((metric) => (
                <th
                  scope="col"
                  key={metric.key}
                  className={heading}
                  title={metric.description}
                  aria-sort={metric.format === 'text' ? undefined : sortState(metric.key)}
                >
                  {metric.format === 'text' ? (
                    metric.label
                  ) : (
                    <button
                      type="button"
                      className="inline-flex items-center gap-2"
                      onClick={() => sort(metric.key)}
                    >
                      {metric.label} <SortIcon metricKey={metric.key} />
                    </button>
                  )}
                </th>
              ))}
              <th scope="col" className={`${heading} sm:right-0`}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {pageRows.map((row) => {
              const action = reportAction?.(row.configId)
              return (
                <tr
                  key={row.key}
                  data-config-id={row.configId}
                  data-proposal-number={row.number}
                  className={view.selectedConfigId === row.configId ? 'bg-muted/30' : undefined}
                >
                  <th
                    scope="row"
                    className={`${cell} sticky left-0 z-10 bg-background font-normal`}
                  >
                    {row.number + 1}
                    {row.candidate && row.configId === experiment.recommendation_id && (
                      <span className="ml-2 text-xs text-primary">Best by objective</span>
                    )}
                  </th>
                  {view.scope === 'all' && (
                    <td className={cell}>
                      {row.status === 'rejected'
                        ? 'Allocation rejected'
                        : row.status === 'reused'
                          ? 'Reused'
                          : 'Completed'}
                    </td>
                  )}
                  {visible.map((metric) => (
                    <td
                      className={cell}
                      key={metric.key}
                      title={
                        !row.candidate || trialMetricValue(metric, row.candidate) == null
                          ? (row.candidate?.analysis?.unavailable[metric.key] ?? metric.description)
                          : undefined
                      }
                    >
                      {trialMetricText(
                        metric,
                        metric.key === 'objective_score'
                          ? row.score
                          : row.candidate
                            ? trialMetricValue(metric, row.candidate)
                            : null
                      )}
                    </td>
                  ))}
                  <td className={`${cell} bg-background sm:sticky sm:right-0 sm:z-10`}>
                    {row.candidate ? (
                      <>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          aria-haspopup="dialog"
                          onClick={(event) => inspect(row, event.currentTarget)}
                        >
                          {onInspect ? 'View details' : 'View settings'}
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={
                            onReport
                              ? (action?.disabled ?? (rerunning || readOnly))
                              : rerunning || readOnly
                          }
                          onClick={() => {
                            updateView({ selectedConfigId: row.configId })
                            if (onReport) onReport(row.configId)
                            else onRerun(row.configId)
                          }}
                        >
                          {onReport ? (action?.label ?? 'Prepare report') : 'Backtest this'}
                        </Button>
                      </>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        {row.status === 'rejected' ? 'Not evaluated' : 'Saved settings unavailable'}
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
            {!rows.length && (
              <tr>
                <td
                  colSpan={visible.length + (view.scope === 'all' ? 3 : 2)}
                  className="p-8 text-center text-sm text-muted-foreground"
                >
                  {view.scope === 'all' && !experiment.trials
                    ? 'Proposal history was not saved for this study.'
                    : view.scope === 'all'
                      ? 'No recorded proposals to show.'
                      : 'No completed trials to show.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
      {rows.length > pageSize && (
        <div className="flex items-center justify-end gap-3 text-xs text-muted-foreground">
          <span>
            {page * pageSize + 1}–{Math.min(rows.length, (page + 1) * pageSize)} of {rows.length}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={page === 0}
            onClick={() => updateView({ page: page - 1 })}
          >
            Previous
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={(page + 1) * pageSize >= rows.length}
            onClick={() => updateView({ page: page + 1 })}
          >
            Next
          </Button>
        </div>
      )}

      <Dialog
        open={Boolean(trial)}
        onOpenChange={(open) => {
          if (!open) setDetail(null)
        }}
      >
        <DialogContent
          className="max-h-[85dvh] overflow-y-auto sm:max-w-2xl motion-reduce:animate-none"
          onOpenAutoFocus={(event) => {
            event.preventDefault()
            settingsTitle.current?.focus({ preventScroll: true })
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            if (settingsTrigger.current?.isConnected)
              settingsTrigger.current.focus({ preventScroll: true })
          }}
        >
          <DialogHeader>
            <DialogTitle
              ref={settingsTitle}
              tabIndex={-1}
              className="pr-6 leading-snug outline-none"
            >
              Trial {trial ? (detailNumber ?? trial.trial_number) + 1 : ''} settings
            </DialogTitle>
            <DialogDescription>The trading settings tested in this trial.</DialogDescription>
          </DialogHeader>
          <Tabs defaultValue="settings" key={trial?.config_id}>
            {trial?.analysis && experiment.analysis_catalog && (
              <TabsList>
                <TabsTrigger value="settings">Settings</TabsTrigger>
                <TabsTrigger value="statistics">Statistics</TabsTrigger>
              </TabsList>
            )}
            <TabsContent value="settings">
              <section className="divide-y" aria-label="Trial strategy settings">
                {trial?.strategies.map((strategy) => (
                  <div key={strategy.id}>{renderSettings(strategy)}</div>
                ))}
              </section>
            </TabsContent>
            {trial?.analysis && experiment.analysis_catalog && (
              <TabsContent value="statistics" className="pt-3">
                <AnalysisMetricTable
                  catalog={experiment.analysis_catalog}
                  analysis={trial.analysis}
                />
              </TabsContent>
            )}
          </Tabs>
          <DialogFooter className="gap-2">
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Close settings
              </Button>
            </DialogClose>
            <Button
              type="button"
              disabled={rerunning || readOnly || !trial}
              onClick={() => {
                if (trial) {
                  setDetail(null)
                  onRerun(trial.config_id)
                }
              }}
            >
              Backtest this
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
