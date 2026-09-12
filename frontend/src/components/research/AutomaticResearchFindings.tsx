import { Check, ChevronDown, FlaskConical } from 'lucide-react'
import type { AutomaticResearchFindings as Findings } from '@/api/portfolioResearch'
import { researchDates } from './researchPresentation'

const number = (value: unknown, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix}`
    : '—'
const periods = [
  ['search', 'Search'],
  ['check1', 'First check'],
  ['check2', 'Second check'],
  ['final', 'Final check'],
] as const

export function AutomaticResearchFindings({ findings }: { findings: Findings }) {
  const final = findings.final
  const selected = findings.checks.find((check) => check.config_id === findings.selected_config_id)
  const comparisons: Array<[string, Record<string, number | string | null>]> = final
    ? [
        ['Original settings', final.baseline_summary],
        ...(!findings.selected_is_baseline
          ? [
              [
                selected ? `Trial ${selected.trial_number + 1}` : 'Selected settings',
                final.summary,
              ] as [string, Record<string, number | string | null>],
            ]
          : []),
      ]
    : []
  return (
    <section
      aria-label="Automatic research findings"
      className="relative min-w-0 overflow-hidden rounded-xl border border-cyan-500/20 bg-gradient-to-br from-cyan-500/5 via-background to-violet-500/5 p-5 sm:p-6 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 rounded-full border border-cyan-500/25 p-2" aria-hidden="true">
          {findings.status === 'supported' ? (
            <Check className="size-4" />
          ) : (
            <FlaskConical className="size-4" />
          )}
        </span>
        <div className="min-w-0 space-y-1">
          <p className="text-xs font-medium tracking-wide text-muted-foreground">
            Exploratory research
          </p>
          <h3 className="text-lg font-semibold">{findings.headline}</h3>
          <p className="max-w-3xl text-sm text-muted-foreground">
            {findings.selected_is_baseline
              ? 'The original settings remain the reference.'
              : 'Checked on later dates and with higher trading costs.'}
          </p>
        </div>
      </div>
      {final && (
        <div className="mt-5 space-y-3">
          <p className="text-xs text-muted-foreground">
            Final check ·{' '}
            {researchDates(findings.recipe.periods.final.from, findings.recipe.periods.final.to)}
          </p>
          <div className="overflow-x-auto">
            <table
              className="w-full text-left text-sm tabular-nums"
              aria-label="Final period comparison"
            >
              <thead>
                <tr className="text-xs text-muted-foreground">
                  <th className="pb-3 font-medium">Settings</th>
                  <th className="px-3 pb-3 text-right font-medium">Return</th>
                  <th className="px-3 pb-3 text-right font-medium">Max drawdown</th>
                  <th className="pb-3 pl-3 text-right font-medium">Closed trades</th>
                </tr>
              </thead>
              <tbody>
                {comparisons.map(([label, summary]) => (
                  <tr key={label} className="border-t border-border/60">
                    <th scope="row" className="whitespace-nowrap py-3 pr-3 font-medium">
                      {label}
                    </th>
                    <td className="whitespace-nowrap px-3 py-3 text-right">
                      {number(summary.net_return_pct, '%')}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-right">
                      {number(summary.max_drawdown_pct, '%')}
                    </td>
                    <td className="whitespace-nowrap py-3 pl-3 text-right">
                      {number(summary.closed_trades)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <details className="group mt-3 border-t border-border/60 pt-3 text-xs">
        <summary className="flex w-fit cursor-pointer list-none items-center gap-1.5 text-muted-foreground hover:text-foreground">
          Research details{' '}
          <ChevronDown
            className="size-3.5 transition-transform group-open:rotate-180 motion-reduce:transition-none"
            aria-hidden="true"
          />
        </summary>
        <div className="mt-4 space-y-4">
          <p className="text-muted-foreground">{findings.selection_basis}</p>
          <dl className="grid gap-4 sm:grid-cols-4">
            {periods.map(([key, label]) => (
              <div key={key} className="border-t-2 border-cyan-500/30 pt-2">
                <dt className="font-medium">{label}</dt>
                <dd className="mt-1 text-muted-foreground">
                  {researchDates(
                    findings.recipe.periods[key].from,
                    findings.recipe.periods[key].to
                  )}
                </dd>
              </div>
            ))}
          </dl>
          <p className="text-muted-foreground">
            {number(findings.counts.proposals)} proposals · {number(findings.counts.simulations)}{' '}
            simulations · {number(findings.counts.finalists)} finalists
          </p>
          {findings.checks.length > 0 && (
            <div className="divide-y">
              {findings.checks.map((check) => (
                <div key={check.config_id} className="flex flex-wrap justify-between gap-2 py-2">
                  <span>Trial {check.trial_number + 1}</span>
                  <span className="max-w-xl text-muted-foreground">
                    {check.eligible
                      ? 'Passed later-date and cost checks'
                      : check.reasons.join(' · ') || 'Did not pass the checks'}
                  </span>
                </div>
              ))}
            </div>
          )}
          {final?.reasons && final.reasons.length > 0 && (
            <p className="text-muted-foreground">Final check: {final.reasons.join(' · ')}</p>
          )}
          <p className="text-muted-foreground">
            The detailed report covers the search dates. Final-check values above use the same later
            dates and starting cash for both settings.
          </p>
          {findings.unsupported_families.length > 0 && (
            <p className="text-muted-foreground">
              Not included in this run: {findings.unsupported_families.join(', ')}.
            </p>
          )}
        </div>
      </details>
    </section>
  )
}
