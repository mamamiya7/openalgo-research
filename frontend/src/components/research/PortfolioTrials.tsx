import { Columns3 } from 'lucide-react'
import { type ReactNode, useMemo, useRef, useState } from 'react'
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

interface Props {
  experiment: NonNullable<PortfolioResult['experiment']>
  page: number
  onPageChange: (page: number) => void
  onRerun: (trialId: string) => void
  rerunning: boolean
  readOnly: boolean
  renderSettings: (strategy: PortfolioSettings) => ReactNode
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
  page,
  onPageChange: setPage,
  onRerun,
  rerunning,
  readOnly,
  renderSettings,
  owner,
}: Props & { owner: string }) {
  const [detail, setDetail] = useState<string | null>(null)
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
  const trial = experiment.rows.find((row) => row.config_id === detail)
  const restoreDefaults = () =>
    setDraft((defaults.length ? defaults : available.slice(0, 1)).map((metric) => metric.key))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {experiment.counts.evaluated_this_pass} distinct portfolios
          {experiment.counts.rejected_allocations > 0
            ? ` · ${experiment.counts.rejected_allocations} allocation combinations excluded`
            : ''}
          {experiment.counts.reused_trials > 0
            ? ` · ${experiment.counts.reused_trials} repeated proposals reused`
            : ''}
        </p>
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

      <section
        className="max-h-[60vh] overflow-auto rounded-lg border"
        aria-label="Trial comparison"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users must be able to scroll this bounded comparison table.
        tabIndex={0}
      >
        <table className="w-full">
          <caption className="sr-only">Optimization trials</caption>
          <thead>
            <tr>
              <th scope="col" className={`${heading} left-0 z-30`}>
                Trial
              </th>
              {visible.map((metric) => (
                <th scope="col" key={metric.key} className={heading} title={metric.description}>
                  {metric.label}
                </th>
              ))}
              <th scope="col" className={`${heading} sm:right-0`}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {experiment.rows.slice(page * pageSize, (page + 1) * pageSize).map((row) => (
              <tr key={row.config_id}>
                <th scope="row" className={`${cell} sticky left-0 z-10 bg-background font-normal`}>
                  {row.trial_number + 1}
                  {row.config_id === experiment.recommendation_id && (
                    <span className="ml-2 text-xs text-primary">Selected</span>
                  )}
                </th>
                {visible.map((metric) => (
                  <td
                    className={cell}
                    key={metric.key}
                    title={
                      trialMetricValue(metric, row) == null
                        ? (row.analysis?.unavailable[metric.key] ?? metric.description)
                        : undefined
                    }
                  >
                    {trialMetricText(metric, trialMetricValue(metric, row))}
                  </td>
                ))}
                <td className={`${cell} bg-background sm:sticky sm:right-0 sm:z-10`}>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-haspopup="dialog"
                    onClick={(event) => {
                      settingsTrigger.current = event.currentTarget
                      setDetail(row.config_id)
                    }}
                  >
                    View settings
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={rerunning || readOnly}
                    onClick={() => onRerun(row.config_id)}
                  >
                    Backtest this
                  </Button>
                </td>
              </tr>
            ))}
            {!experiment.rows.length && (
              <tr>
                <td
                  colSpan={visible.length + 2}
                  className="p-8 text-center text-sm text-muted-foreground"
                >
                  No completed trials to show.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
      {experiment.rows.length > pageSize && (
        <div className="flex items-center justify-end gap-3 text-xs text-muted-foreground">
          <span>
            {page * pageSize + 1}–{Math.min(experiment.rows.length, (page + 1) * pageSize)} of{' '}
            {experiment.rows.length}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            Previous
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={(page + 1) * pageSize >= experiment.rows.length}
            onClick={() => setPage(page + 1)}
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
              Trial {trial ? trial.trial_number + 1 : ''} settings
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
