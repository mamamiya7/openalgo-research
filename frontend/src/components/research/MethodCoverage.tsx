import { useQuery } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { ChevronDown } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import {
  type ResearchMethodCoverageRow,
  researchMethodCoverage,
} from '@/api/researchMethodCoverage'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/authStore'

const fitLabels: Record<string, string> = {
  fit1: 'Fit 1',
  fit2: 'Fit 2',
  fit3: 'Fit 3',
  search: 'Final fit',
}
const requirementLabels: Record<string, string> = {
  stock_daily_close: 'Daily stock closes',
  stock_daily_ohlc: 'Daily stock OHLC',
  NIFTY_NSE_INDEX_daily_close: 'Daily Nifty closes',
  registered_component_inputs: 'Inputs for each registered condition',
}
const knownCount = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0
const count = (value: unknown) => (knownCount(value) ? value.toLocaleString('en-IN') : '—')

/** This control is available only for a saved, supported recipe, never a draft. */
export function MethodCoverage({
  jobId,
  recipeVersion,
}: {
  jobId?: string
  recipeVersion?: string
}) {
  const owner = useAuthStore((state) => state.user?.username ?? 'account')
  if (
    !jobId ||
    ![
      'automatic-hypotheses-v8',
      'automatic-hypotheses-v9',
      'automatic-hypotheses-v10',
      'automatic-hypotheses-v11',
      'automatic-hypotheses-v12',
    ].includes(recipeVersion ?? '')
  )
    return null
  return (
    <MethodDisclosure
      key={JSON.stringify([owner, jobId, recipeVersion])}
      owner={owner}
      jobId={jobId}
      recipeVersion={recipeVersion ?? ''}
    />
  )
}

function MethodDisclosure({
  owner,
  jobId,
  recipeVersion,
}: {
  owner: string
  jobId: string
  recipeVersion: string
}) {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  return (
    <div className="space-y-3">
      <button
        type="button"
        ref={trigger}
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        aria-expanded={open}
        aria-controls={`research-methods-${jobId}`}
        onClick={() => setOpen((value) => !value)}
      >
        Methods considered
        <ChevronDown
          className={`size-3.5 transition-transform motion-reduce:transition-none ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div id={`research-methods-${jobId}`}>
          <RecordedMethods
            owner={owner}
            jobId={jobId}
            recipeVersion={recipeVersion}
            onReloaded={() => {
              if (
                trigger.current?.isConnected &&
                trigger.current.getAttribute('aria-expanded') === 'true' &&
                document.activeElement === document.body
              )
                trigger.current.focus()
            }}
          />
        </div>
      )}
    </div>
  )
}

function RecordedMethods({
  owner,
  jobId,
  onReloaded,
  recipeVersion,
}: {
  owner: string
  jobId: string
  onReloaded: () => void
  recipeVersion: string
}) {
  const [fit, setFit] = useState('all')
  const [selected, setSelected] = useState<string | null>(null)
  const retried = useRef(false)
  const query = useQuery({
    queryKey: ['research-method-coverage', owner, jobId, recipeVersion],
    queryFn: ({ signal }) => researchMethodCoverage.get(jobId, signal),
    retry: false,
    gcTime: 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  useEffect(() => {
    if (query.isSuccess && retried.current) {
      retried.current = false
      onReloaded()
    }
  }, [query.isSuccess, onReloaded])
  if (query.isPending)
    return <output className="block text-xs text-muted-foreground">Loading method checks…</output>
  if (query.isError) {
    const changed =
      isAxiosError(query.error) &&
      query.error.response?.status === 409 &&
      query.error.response.data?.code === 'method_coverage_changed'
    return (
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <p role="alert" className="text-muted-foreground">
          {changed
            ? 'Saved research changed. Reload the method checks.'
            : 'Method checks could not be loaded.'}
        </p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={query.isFetching}
          onClick={() => {
            retried.current = true
            void query.refetch()
          }}
        >
          {changed ? 'Reload methods' : 'Try again'}
        </Button>
      </div>
    )
  }
  const view = query.data
  const coverage = view?.coverage
  if (
    view?.version !== 'research-method-coverage-view-v1' ||
    view.job_id !== jobId ||
    !view.available ||
    coverage?.version !== 'research-method-coverage-v1' ||
    coverage.status !== 'available' ||
    coverage.registry?.version !== 'research-method-registry-v1' ||
    coverage.source?.recipe_version !== recipeVersion ||
    coverage.registry.recipe_version !== recipeVersion
  )
    return (
      <p className="text-xs text-muted-foreground">
        {view?.job_id === jobId && typeof view.reason === 'string' && view.reason
          ? view.reason
          : 'Method details were not recorded for this run.'}
      </p>
    )

  const registry = coverage.registry
  const fits = Array.isArray(coverage.fits) ? coverage.fits.slice(0, 4) : []
  const scope = fit === 'all' ? coverage.families : fits.find((item) => item.id === fit)?.families
  const rows = Array.isArray(scope) ? scope.slice(0, 32) : []
  const allCountsKnown = rows.length > 0 && rows.every((row) => knownCount(row.evaluated))
  const tried = rows.filter((row) => knownCount(row.evaluated) && row.evaluated > 0).length
  const families = Array.isArray(registry.families) ? registry.families.slice(0, 32) : []
  const methods = Array.isArray(registry.methods) ? registry.methods.slice(0, 128) : []

  function unknownEntries(row: ResearchMethodCoverageRow) {
    if (row.data_status !== 'unknown_entries_reported') return null
    return (
      <span className="mt-1 block text-xs font-normal text-muted-foreground">
        Some entry checks lacked feature data
      </span>
    )
  }

  return (
    <section aria-label="Recorded method checks" className="space-y-3 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-medium">
          {allCountsKnown
            ? `${count(tried)} method ${tried === 1 ? 'family' : 'families'} tested`
            : rows.length
              ? 'Recorded method checks'
              : 'No method checks recorded yet.'}
        </p>
        {fits.length > 0 && (
          <label className="flex items-center gap-2 text-muted-foreground">
            <span>Show</span>
            <select
              aria-label="Method checks for"
              value={fit}
              className="h-8 rounded-md border bg-background px-2 text-xs text-foreground"
              onChange={(event) => {
                setFit(event.target.value)
                setSelected(null)
              }}
            >
              <option value="all">All fits</option>
              {fits.map((item) => (
                <option key={item.id} value={item.id}>
                  {fitLabels[item.id] ?? item.id}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {rows.length > 0 && (
        <>
          <div className="max-h-96 overflow-auto rounded-md border">
            <table
              className="w-full min-w-[460px] text-left tabular-nums"
              aria-label="Methods considered"
            >
              <thead className="sticky top-0 z-10 border-b bg-background text-muted-foreground">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Method
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2 text-right font-medium"
                    title="Distinct proposal-linked training accounts, not validation passes"
                  >
                    Tested
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Reused
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2 text-right font-medium"
                    title="Registered conditions without a recorded proposal"
                  >
                    Not tried
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((row) => {
                  const definition = families.find((item) => item.id === row.id)
                  const parts = methods.filter((method) =>
                    definition?.method_ids?.includes(method.id)
                  )
                  const expanded = selected === row.id
                  return (
                    <MethodRow
                      key={row.id}
                      row={row}
                      label={definition?.label ?? row.id}
                      parts={parts}
                      expanded={expanded}
                      onToggle={() => setSelected(expanded ? null : row.id)}
                      unknown={unknownEntries(row)}
                    />
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="text-muted-foreground">
            Tested counts distinct proposal accounts; not tried counts registered conditions without
            proposals. Testing does not mean an improvement was found.
          </p>
        </>
      )}
      {Array.isArray(registry.blocked_families) && registry.blocked_families.length > 0 && (
        <details>
          <summary className="w-fit cursor-pointer text-muted-foreground">
            Not available in this run
          </summary>
          <dl className="mt-3 space-y-3">
            {registry.blocked_families.slice(0, 64).map((item) => (
              <div key={item.id}>
                <dt className="font-medium">{item.label}</dt>
                <dd className="mt-1 text-muted-foreground">{item.reason}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </section>
  )
}

function MethodRow({
  row,
  label,
  parts,
  expanded,
  onToggle,
  unknown,
}: {
  row: ResearchMethodCoverageRow
  label: string
  parts: Array<{
    id: string
    label: string
    formula: string
    requirements: string[]
  }>
  expanded: boolean
  onToggle: () => void
  unknown: React.ReactNode
}) {
  return (
    <>
      <tr>
        <th scope="row" className="px-3 py-3 font-normal">
          {parts.length ? (
            <button
              type="button"
              className="text-left hover:underline underline-offset-4"
              aria-expanded={expanded}
              aria-controls={`method-detail-${row.id}`}
              onClick={onToggle}
            >
              {label}
            </button>
          ) : (
            label
          )}
          {unknown}
        </th>
        <td className="px-3 py-3 text-right">{count(row.evaluated)}</td>
        <td className="px-3 py-3 text-right">{count(row.reused)}</td>
        <td className="px-3 py-3 text-right">{count(row.untested)}</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={4} className="bg-muted/20 px-3 py-3">
            <div id={`method-detail-${row.id}`} className="space-y-3">
              {parts.length > 1 && (
                <p className="text-muted-foreground">All conditions must be met.</p>
              )}
              {parts.map((method) => (
                <div key={method.id}>
                  <p className="font-medium">{method.label}</p>
                  {typeof method.formula === 'string' && (
                    <p className="mt-1 text-muted-foreground">{method.formula}</p>
                  )}
                  {Array.isArray(method.requirements) && method.requirements.length > 0 && (
                    <p className="mt-1 text-muted-foreground">
                      Needs:{' '}
                      {method.requirements
                        .map((item) => requirementLabels[item] ?? item)
                        .join(' · ')}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
