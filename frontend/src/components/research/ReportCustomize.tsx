import { SlidersHorizontal } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { PortfolioResult } from '@/api/portfolioResearch'
import { defaultReportPreferences, type ReportPreferences } from '@/api/reportPreferences'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { availableReportMetrics, reportMetricDescription } from './portfolioReportMetrics'

export function ReportCustomize({
  result,
  value,
  disabled,
  saving,
  onSave,
}: {
  result: PortfolioResult
  value: ReportPreferences
  disabled: boolean
  saving: boolean
  onSave: (changes: Partial<ReportPreferences>) => Promise<boolean>
}) {
  const metrics = useMemo(() => availableReportMetrics(result), [result])
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState('headline_metrics')
  const [search, setSearch] = useState('')
  const [draft, setDraft] = useState({
    headline_metrics: value.headline_metrics,
    statistic_metrics: value.statistic_metrics,
  })
  const [failed, setFailed] = useState(false)
  const groups = [...new Set(metrics.map((metric) => metric.group))]
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (saving) return
        if (next) {
          setDraft({
            headline_metrics: [...value.headline_metrics],
            statistic_metrics: [...value.statistic_metrics],
          })
          setTab('headline_metrics')
          setSearch('')
          setFailed(false)
        }
        setOpen(next)
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" variant="ghost" disabled={disabled}>
          <SlidersHorizontal className="mr-2 size-3.5" aria-hidden="true" />
          Customize
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-xl motion-reduce:animate-none">
        <DialogHeader>
          <DialogTitle>Customize report</DialogTitle>
          <DialogDescription>
            Choose the numbers you see first. Saved with your account.
          </DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="headline_metrics">Headline values</TabsTrigger>
            <TabsTrigger value="statistic_metrics">Beside the chart</TabsTrigger>
          </TabsList>
          <Input
            className="mt-4"
            aria-label="Find a report statistic"
            placeholder="Find a statistic…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {(['headline_metrics', 'statistic_metrics'] as const).map((key) => {
            const limit = key === 'headline_metrics' ? 6 : 24
            return (
              <TabsContent key={key} value={key} className="space-y-5 pt-2">
                <p className="text-xs text-muted-foreground">
                  {draft[key].length} of {limit} selected
                </p>
                <div className="max-h-[40dvh] space-y-5 overflow-y-auto pr-2">
                  {draft[key].some((id) => !metrics.some((metric) => metric.key === id)) && (
                    <fieldset className="space-y-3">
                      <legend className="mb-2 text-xs font-medium text-muted-foreground">
                        Saved for other reports
                      </legend>
                      {draft[key]
                        .filter((id) => !metrics.some((metric) => metric.key === id))
                        .map((id) => (
                          <label
                            key={id}
                            className="flex items-center gap-3 text-sm"
                            title="This statistic is not included in this report. Your selection is kept for reports that include it."
                          >
                            <Checkbox
                              aria-label={`Keep saved statistic ${id}`}
                              checked
                              disabled={
                                saving || (key === 'headline_metrics' && draft[key].length === 1)
                              }
                              onCheckedChange={() =>
                                setDraft((previous) => ({
                                  ...previous,
                                  [key]: previous[key].filter((item) => item !== id),
                                }))
                              }
                            />
                            <span className="break-words">
                              {id
                                .replace(/^vectorbt_/, 'VectorBT · ')
                                .replace(/^nautilus_/, 'NautilusTrader · ')
                                .replaceAll('_', ' ')}
                            </span>
                          </label>
                        ))}
                    </fieldset>
                  )}
                  {groups.map((group) => {
                    const choices = metrics.filter(
                      (metric) =>
                        metric.group === group &&
                        `${metric.label} ${metric.source ?? ''} ${metric.description}`
                          .toLowerCase()
                          .includes(search.trim().toLowerCase())
                    )
                    if (!choices.length) return null
                    return (
                      <fieldset key={group} className="space-y-3">
                        <legend className="mb-2 text-xs font-medium text-muted-foreground">
                          {group}
                        </legend>
                        {choices.map((metric) => {
                          const selected = draft[key].includes(metric.key)
                          return (
                            <label
                              key={metric.key}
                              className="flex items-start gap-3 text-sm"
                              title={reportMetricDescription(result, metric)}
                            >
                              <Checkbox
                                aria-label={`${metric.label} · ${metric.source ?? metric.group}`}
                                checked={selected}
                                disabled={
                                  saving ||
                                  (!selected && draft[key].length >= limit) ||
                                  (key === 'headline_metrics' &&
                                    selected &&
                                    draft[key].length === 1)
                                }
                                onCheckedChange={(checked) =>
                                  setDraft((previous) => ({
                                    ...previous,
                                    [key]: checked
                                      ? [...previous[key], metric.key]
                                      : previous[key].filter((id) => id !== metric.key),
                                  }))
                                }
                              />
                              <span>
                                {metric.label}
                                <span className="ml-2 text-xs text-muted-foreground">
                                  {metric.source}
                                </span>
                              </span>
                            </label>
                          )
                        })}
                      </fieldset>
                    )
                  })}
                </div>
              </TabsContent>
            )
          })}
        </Tabs>
        {failed && (
          <p role="alert" className="text-sm text-destructive">
            Your choices were not saved. Close this dialog to retry or reload preferences.
          </p>
        )}
        <DialogFooter className="gap-2 sm:justify-between">
          <Button
            variant="ghost"
            disabled={saving}
            onClick={() =>
              setDraft({
                headline_metrics: defaultReportPreferences.headline_metrics,
                statistic_metrics: defaultReportPreferences.statistic_metrics,
              })
            }
          >
            Use defaults
          </Button>
          <Button
            disabled={saving}
            onClick={async () => {
              setFailed(false)
              if (await onSave(draft)) setOpen(false)
              else setFailed(true)
            }}
          >
            {saving ? 'Saving…' : 'Apply'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
