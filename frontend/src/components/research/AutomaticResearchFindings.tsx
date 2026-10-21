import { Check, ChevronDown, FlaskConical } from 'lucide-react'
import type {
  DevelopmentEvidenceMetadata,
  AutomaticResearchFindings as Findings,
  PortfolioSettings,
  WalkForwardFindings,
} from '@/api/portfolioResearch'
import { AnalysisFigure } from './AnalysisCharts'
import { MethodCoverage } from './MethodCoverage'
import { researchDates, strategyRuleSummary } from './researchPresentation'
import { researchRecipeLabel } from './researchRecipeLabels'

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

// Saved receipts may come from an older server: never infer completed checks from a recipe name.
function recordedWalkForward(findings: Findings): WalkForwardFindings | undefined {
  const receipt = findings.walk_forward
  if (
    ![
      'automatic-hypotheses-v8',
      'automatic-hypotheses-v9',
      'automatic-hypotheses-v10',
      'automatic-hypotheses-v11',
      'automatic-hypotheses-v12',
      'automatic-hypotheses-v13',
    ].includes(findings.version) ||
    receipt?.version !== 'research-walk-forward-findings-v1' ||
    !receipt ||
    !['supported', 'not_supported'].includes(receipt.status) ||
    !Array.isArray(receipt.folds) ||
    receipt.folds.length !== 3
  )
    return undefined
  const date = (value: unknown): value is string =>
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    !Number.isNaN(Date.parse(value)) &&
    new Date(value).toISOString().slice(0, 10) === value
  const summary = (value: unknown) =>
    value !== null && typeof value === 'object' && !Array.isArray(value)
  const period = (value: { from: string; to: string } | undefined) =>
    Boolean(value && date(value.from) && date(value.to) && value.from <= value.to)
  if (
    !period(receipt.final_fit) ||
    typeof receipt.selection_policy !== 'string' ||
    !receipt.folds.every(
      (fold, index) =>
        fold &&
        typeof fold.id === 'string' &&
        period(fold.fit) &&
        period(fold.test) &&
        fold.fit.to < fold.test.from &&
        (!index || receipt.folds[index - 1].test.to < fold.test.from) &&
        typeof fold.selected_config_id === 'string' &&
        typeof fold.eligible === 'boolean' &&
        Array.isArray(fold.reasons) &&
        fold.reasons.every((reason) => typeof reason === 'string') &&
        summary(fold.summary) &&
        summary(fold.baseline_summary) &&
        summary(fold.stress_summary) &&
        summary(fold.stress_baseline_summary) &&
        Number.isInteger(fold.training?.proposals) &&
        fold.training.proposals > 0 &&
        Number.isInteger(fold.training?.unique_candidates) &&
        fold.training.unique_candidates > 0
    )
  )
    return undefined
  return receipt
}

function WalkForwardChecks({ receipt }: { receipt: WalkForwardFindings }) {
  return (
    <section aria-label="Walk-forward checks" className="space-y-3">
      <p className="text-muted-foreground">
        Each window uses settings chosen only from its earlier fit dates. Accounts restart with the
        same cash; these results are not a continuous portfolio.
      </p>
      <div className="overflow-x-auto">
        <table
          className="w-full text-left tabular-nums"
          aria-label="Walk-forward window comparison"
        >
          <thead>
            <tr className="text-muted-foreground">
              <th className="pb-2 pr-4 font-medium">Fit dates / later check</th>
              <th className="pb-2 pr-4 font-medium">Selected settings</th>
              <th className="pb-2 pr-4 text-right font-medium">Return</th>
              <th className="pb-2 pr-4 text-right font-medium">Max drawdown</th>
              <th className="pb-2 pr-4 text-right font-medium">Higher-cost return</th>
              <th className="pb-2 font-medium">Check</th>
            </tr>
          </thead>
          <tbody>
            {receipt.folds.map((fold, index) => (
              <tr key={fold.id} className="border-t border-border/60">
                <th scope="row" className="whitespace-nowrap py-3 pr-4 font-normal">
                  <span className="block font-medium">Window {index + 1}</span>
                  <span className="block text-muted-foreground">
                    Fit {researchDates(fold.fit.from, fold.fit.to)}
                  </span>
                  <span className="block">Check {researchDates(fold.test.from, fold.test.to)}</span>
                </th>
                <td className="py-3 pr-4">
                  {fold.selected_condition || 'Selected settings'}
                  {Array.isArray(fold.selected_settings) &&
                    fold.selected_settings.map((strategy) =>
                      strategy?.config && typeof strategy.config === 'object' ? (
                        <span
                          key={strategy.id}
                          className="mt-1 block min-w-48 text-muted-foreground"
                        >
                          {fold.selected_settings!.length > 1 && `${strategy.name} · `}
                          {strategyRuleSummary(
                            {
                              config: strategy.config,
                              execution_policy: strategy.execution_policy,
                              search: {},
                            },
                            false,
                            true
                          )}
                        </span>
                      ) : null
                    )}
                  <span className="block text-muted-foreground">
                    {fold.selected_config_id.slice(0, 12)}
                  </span>
                </td>
                <td className="whitespace-nowrap py-3 pr-4 text-right">
                  {number(fold.summary.net_return_pct, '%')}
                  <span className="block text-muted-foreground">
                    Original {number(fold.baseline_summary.net_return_pct, '%')}
                  </span>
                </td>
                <td className="whitespace-nowrap py-3 pr-4 text-right">
                  {number(fold.summary.max_drawdown_pct, '%')}
                  <span className="block text-muted-foreground">
                    Original {number(fold.baseline_summary.max_drawdown_pct, '%')}
                  </span>
                </td>
                <td className="whitespace-nowrap py-3 pr-4 text-right">
                  {number(fold.stress_summary.net_return_pct, '%')}
                  <span className="block text-muted-foreground">
                    Original {number(fold.stress_baseline_summary.net_return_pct, '%')}
                  </span>
                </td>
                <td className="py-3">
                  {fold.eligible ? 'Passed' : 'Not supported'}
                  {fold.reasons.length > 0 && (
                    <span className="block text-muted-foreground">{fold.reasons.join(' · ')}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-muted-foreground">
        Final fit · {researchDates(receipt.final_fit.from, receipt.final_fit.to)}. Settings were
        frozen before the reserved final check; that check does not choose or rerank settings.
      </p>
    </section>
  )
}

export function AutomaticResearchFindings({
  findings,
  jobId,
  selectedSettings,
  developmentEvidence,
}: {
  findings: Findings
  jobId?: string
  selectedSettings?: PortfolioSettings[]
  developmentEvidence?: DevelopmentEvidenceMetadata
}) {
  const final = findings.final
  const walkForward = recordedWalkForward(findings)
  const selected = findings.checks.find((check) => check.config_id === findings.selected_config_id)
  const retainedHistories = developmentEvidence
    ? Object.values(developmentEvidence.entries).filter((entry) => entry.status === 'retained')
        .length
    : 0
  const incompleteHistories =
    developmentEvidence?.status === 'partial' ||
    Object.values(developmentEvidence?.entries ?? {}).some(
      (entry) => entry.status === 'unavailable'
    )
  const historySummary = !developmentEvidence
    ? undefined
    : developmentEvidence.status === 'unavailable'
      ? 'Development account histories unavailable'
      : retainedHistories > 0
        ? `${retainedHistories.toLocaleString('en-IN')} development account ${retainedHistories === 1 ? 'history' : 'histories'} saved${developmentEvidence.status === 'collecting' ? ' · collection in progress' : incompleteHistories ? ' · some histories unavailable' : ''}`
        : developmentEvidence.status === 'collecting'
          ? 'Development account histories are being saved'
          : 'Development account histories unavailable'
  const comparisons: Array<[string, Record<string, number | string | null>]> = final
    ? [
        ['Original settings', final.baseline_summary],
        ...(!findings.selected_is_baseline
          ? [
              [
                findings.selected_condition ||
                  (selected?.trial_number != null
                    ? `Trial ${selected.trial_number + 1}`
                    : 'Selected settings'),
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
            {final ? 'Automatic research' : 'Exploratory research'}
          </p>
          <h3 className="text-lg font-semibold">{findings.headline}</h3>
          <p className="max-w-3xl text-sm text-muted-foreground">
            {findings.selected_is_baseline
              ? 'No reliable improvement was found. The original settings remain the reference, not a trading recommendation.'
              : final
                ? 'Checked on later dates and with higher trading costs.'
                : 'Best settings found on the available research dates.'}
          </p>
          {walkForward && (
            <p className="text-sm text-muted-foreground">Refit and checked on 3 later windows.</p>
          )}
          {findings.selected_condition && (
            <p className="text-sm">Entry condition: {findings.selected_condition}</p>
          )}
          {[
            'automatic-hypotheses-v6',
            'automatic-hypotheses-v7',
            'automatic-hypotheses-v8',
            'automatic-hypotheses-v9',
            'automatic-hypotheses-v10',
            'automatic-hypotheses-v11',
            'automatic-hypotheses-v12',
            'automatic-hypotheses-v13',
          ].includes(findings.version) &&
            selectedSettings?.map((strategy) => (
              <p key={strategy.id} className="text-sm">
                {selectedSettings.length > 1 && `${strategy.name} · `}
                {strategyRuleSummary(
                  {
                    config: strategy.config,
                    execution_policy: strategy.execution_policy,
                    search: {},
                  },
                  false,
                  true
                )}
              </p>
            ))}
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
          <p className="font-medium">{researchRecipeLabel(findings.version)}</p>
          <p className="text-muted-foreground">{findings.selection_basis}</p>
          <MethodCoverage jobId={jobId} recipeVersion={findings.version} />
          {historySummary && (
            <p className="text-muted-foreground">
              {historySummary}
              {developmentEvidence?.reserved_final_excluded ? ' · final check kept separate' : ''}
            </p>
          )}
          {findings.comparison?.status === 'available' && findings.comparison.chart && (
            <section aria-label="Matching final-period curves" className="space-y-2">
              <AnalysisFigure chart={findings.comparison.chart} deferred height={280} />
              <p className="text-muted-foreground">
                Same final dates, price cohort, cash and costs; recorded observations only.
                {findings.comparison.displayed !== findings.comparison.observations
                  ? ` ${findings.comparison.displayed} of ${findings.comparison.observations} points shown.`
                  : ''}
              </p>
            </section>
          )}
          {findings.comparison?.status === 'unavailable' && (
            <p className="text-muted-foreground">Comparison chart: {findings.comparison.reason}</p>
          )}
          {findings.recommendation_checks?.version === 'research-recommendation-evidence-v1' && (
            <section aria-label="Recommendation evidence" className="space-y-2">
              <p className="font-medium">Development robustness checks</p>
              <p>
                {findings.recommendation_checks.status === 'supported'
                  ? 'Daily return, higher-cost and nearby-setting checks passed.'
                  : 'The evidence did not qualify a stronger recommendation; unchanged settings remain the reference.'}
              </p>
              {findings.recommendation_checks.reasons.map((reason) => (
                <p key={reason} className="text-muted-foreground">
                  {reason}
                </p>
              ))}
              {findings.recommendation_checks.uncertainty?.ranges?.map((range) => (
                <p key={range.block_sessions} className="text-muted-foreground tabular-nums">
                  {range.block_sessions}-session blocks: {number(range.lower_bps)} to{' '}
                  {number(range.upper_bps)} basis points per day above the reference.
                </p>
              ))}
              <p className="text-muted-foreground">
                Historical sensitivity only; this is not a probability of future profit.
              </p>
            </section>
          )}
          {walkForward ? (
            <WalkForwardChecks receipt={walkForward} />
          ) : findings.recipe.validation_mode === 'exploratory_only' ? (
            <p>No untouched later check: the signal history is too short for this holding range.</p>
          ) : (
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
          )}
          <p className="text-muted-foreground">
            {number(findings.counts.proposals)} proposals · {number(findings.counts.simulations)}{' '}
            simulations · {number(findings.counts.finalists)} finalist
            {findings.counts.finalists === 1 ? '' : 's'}
          </p>
          {[
            'automatic-hypotheses-v10',
            'automatic-hypotheses-v11',
            'automatic-hypotheses-v12',
            'automatic-hypotheses-v13',
          ].includes(findings.version) &&
            findings.execution_research &&
            [
              'research-execution-findings-v1',
              'research-execution-findings-v2',
              'research-execution-findings-v3',
            ].includes(findings.execution_research.version) && (
              <div className="overflow-x-auto">
                <p className="mb-2 font-medium">Sizing comparison · development dates</p>
                <table
                  className="w-full text-left tabular-nums"
                  aria-label="Original-sizing development comparison"
                >
                  <thead>
                    <tr>
                      <th className="p-2 font-medium">Fit</th>
                      <th className="p-2 font-medium">Selected sizing return</th>
                      <th className="p-2 font-medium">Original sizing return</th>
                    </tr>
                  </thead>
                  <tbody>
                    {findings.execution_research.sizing_controls.map((control, index) => (
                      <tr key={control.period} className="border-t border-border/60">
                        <th className="p-2 font-normal" scope="row">
                          {control.period === 'search'
                            ? 'Final development fit'
                            : `Fit ${index + 1}`}
                        </th>
                        <td className="p-2">
                          {number(control.selected_summary.net_return_pct, '%')}
                        </td>
                        <td className="p-2">{number(control.summary.net_return_pct, '%')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-2 text-muted-foreground">
                  Same filters, exits and risk limits; original allocation and order sizing
                  restored. Development comparison only.
                </p>
              </div>
            )}
          {findings.checks.length > 0 && (
            <div className="divide-y">
              {findings.checks.map((check) => (
                <div key={check.config_id} className="flex flex-wrap justify-between gap-2 py-2">
                  <span>
                    {check.trial_number == null
                      ? 'Original exit settings'
                      : `Trial ${check.trial_number + 1}`}
                    {check.condition_label ? ` · ${check.condition_label}` : ''}
                  </span>
                  <span className="max-w-xl text-muted-foreground">
                    {check.eligible
                      ? check.stability?.status === 'stable'
                        ? 'Passed later-date, cost and nearby-setting checks'
                        : 'Passed later-date and cost checks'
                      : check.reasons.join(' · ') || 'Did not pass the checks'}
                    {check.stability && (
                      <span className="block mt-1">
                        Nearby settings: {check.stability.passed} of {check.stability.tested} passed
                        {check.stability.truncated ? ' · sampled subset' : ''}
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}
          {final?.reasons && final.reasons.length > 0 && (
            <p className="text-muted-foreground">Final check: {final.reasons.join(' · ')}</p>
          )}
          <p className="text-muted-foreground">
            {final
              ? walkForward
                ? 'The detailed report covers the final fit on development dates. Final-check values above use the same reserved dates and starting cash for both settings.'
                : 'The detailed report covers the search dates. Final-check values above use the same later dates and starting cash for both settings.'
              : 'These settings were selected on the research dates. Later evidence is needed before calling them validated.'}
          </p>
          {findings.unsupported_families.length > 0 && (
            <p className="text-muted-foreground">
              Not included in this run: {findings.unsupported_families.join(', ')}.
            </p>
          )}
          {findings.version === 'automatic-hypotheses-v13' && (
            <p className="text-muted-foreground">
              Both settings use the same portfolio limits. Volume participation uses prior
              broker-reported history; it is a backtest assumption. Correlation limits new entries
              using prior returns.
            </p>
          )}
        </div>
      </details>
    </section>
  )
}
