import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import type {
  DevelopmentEvidenceMetadata,
  AutomaticResearchFindings as Findings,
} from '@/api/portfolioResearch'
import { researchDefaults } from '@/lib/researchDraft'
import { AutomaticResearchFindings } from './AutomaticResearchFindings'

function findings(): Findings {
  const summary = {
    net_return_pct: 2.8,
    max_drawdown_pct: 0.4,
    closed_trades: 26,
  }
  const baseline = {
    net_return_pct: 0.9,
    max_drawdown_pct: 1.2,
    closed_trades: 30,
  }
  return {
    version: 'automatic-trade-management-v1',
    status: 'supported',
    selected_config_id: 'c-2',
    selected_is_baseline: false,
    headline: 'The improvement held up on later dates',
    selection_basis:
      'Chosen using two earlier checks and higher costs; the final dates were checked afterward.',
    recipe: {
      version: 'automatic-trade-management-v1',
      periods: {
        search: { from: '2026-01-01', to: '2026-05-30' },
        check1: { from: '2026-06-01', to: '2026-06-30' },
        check2: { from: '2026-07-01', to: '2026-07-31' },
        final: { from: '2026-08-01', to: '2026-09-10' },
      },
    },
    baseline,
    checks: [
      {
        config_id: 'c-2',
        trial_number: 8,
        eligible: true,
        reasons: [],
        windows: [],
        stress: { summary, baseline_summary: baseline, score_delta: 1.3 },
        selection_score: 0.8,
      },
    ],
    final: {
      summary,
      baseline_summary: baseline,
      score_delta: 1.3,
      supports: true,
    },
    counts: { proposals: 50, simulations: 64, finalists: 3 },
    unsupported_families: ['Indicators', 'Market regimes'],
  }
}

describe('automatic research findings', () => {
  it('keeps repeated-search evidence collapsed and separate from the primary study', async () => {
    const data = findings()
    data.version = 'automatic-hypotheses-v15'
    data.counts = { proposals: 408, simulations: 410, finalists: 1 }
    data.recommendation_checks = {
      version: 'research-recommendation-evidence-v1',
      status: 'supported',
      reasons: [],
      search_seeds: {
        version: 'research-search-seed-evidence-v1',
        status: 'supported',
        qualified: true,
        reasons: [],
        runs: [0, 1, 2].map((seed) => ({
          seed,
          config_id: `choice-${seed}`,
          proposals: 68,
          unique_accounts: 60,
          return_pct: 5.2 + seed,
          max_drawdown_pct: 1.3,
          edge_pct: 2,
          lower_edge_pct: 1,
          evidence_id: `saved-${seed}`,
        })),
        limitations: [],
      },
    }
    render(<AutomaticResearchFindings findings={data} />)
    const table = screen.getByRole('table', { name: 'Repeated search comparison', hidden: true })
    expect(table).not.toBeVisible()
    await userEvent.click(screen.getByText('Research details'))
    expect(table).toBeVisible()
    expect(within(table).getAllByRole('row')).toHaveLength(4)
    expect(within(table).getByRole('rowheader', { name: 'Search 1' })).toBeVisible()
    expect(within(table).getByRole('rowheader', { name: 'Search 3' })).toBeVisible()
    expect(within(table).getByText('7.2%')).toBeVisible()
    expect(
      screen.getByText(/Repeated searches supported the original development choice/)
    ).toBeVisible()
    expect(screen.getByText(/Repeats check search-path stability/)).toHaveTextContent(
      'never replace it'
    )
    expect(screen.queryByText(/seed/i)).not.toBeInTheDocument()
  })

  it.each([
    'not_supported',
    'insufficient',
    'not_run',
    'unsupported',
  ] as const)('never presents %s repeated-search evidence as qualified', async (status) => {
    const data = findings()
    data.version = 'automatic-hypotheses-v15'
    data.recommendation_checks = {
      version: 'research-recommendation-evidence-v1',
      status: 'not_supported',
      reasons: [],
      search_seeds: {
        version: 'research-search-seed-evidence-v1',
        status,
        qualified: false,
        reasons: ['No stronger recommendation from these checks.'],
        limitations: [],
      },
    }
    render(<AutomaticResearchFindings findings={data} />)
    await userEvent.click(screen.getByText('Research details'))
    const region = screen.getByRole('region', { name: 'Search repeat checks' })
    expect(
      within(region).queryByText(/supported the original development choice/)
    ).not.toBeInTheDocument()
    expect(within(region).getByText('No stronger recommendation from these checks.')).toBeVisible()
    expect(within(region).queryByRole('table')).not.toBeInTheDocument()
    if (status === 'not_run') expect(region).toHaveTextContent('extra searches were not needed')
    if (status === 'insufficient') expect(region).toHaveTextContent('Not enough evidence')
  })

  it.each([
    'automatic-hypotheses-v14',
    'automatic-hypotheses-v15',
  ])('does not invent search repeat checks for saved %s without a receipt', async (version) => {
    const data = findings()
    data.version = version
    render(<AutomaticResearchFindings findings={data} />)
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.queryByRole('region', { name: 'Search repeat checks' })).not.toBeInTheDocument()
  })

  it('keeps actual symbol and date reruns inside details with their original reference values', async () => {
    const data = findings()
    data.version = 'automatic-hypotheses-v14'
    data.recommendation_checks = {
      version: 'research-recommendation-evidence-v1',
      status: 'supported',
      reasons: [],
      sensitivity: {
        version: 'research-recommendation-sensitivity-v1',
        status: 'supported',
        qualified: true,
        reasons: [],
        scenarios: ['Symbol group A', 'Symbol group B', 'Earlier dates', 'Later dates'].map(
          (label, index) => ({
            name: `scenario-${index}`,
            label,
            axis: index < 2 ? 'symbols' : 'dates',
            from: '2026-01-05',
            to: '2026-06-30',
            ...(index < 2 ? { symbols: ['AAA', 'BBB'] } : {}),
            candidate_return_pct: 5.1,
            baseline_return_pct: 2.3,
            return_minus_drawdown_edge_pct: 2.1,
            candidate_max_drawdown_pct: 1.2,
            passes: true,
          })
        ),
      },
    }
    render(<AutomaticResearchFindings findings={data} />)
    const table = screen.getByRole('table', { name: 'Symbol and date comparison', hidden: true })
    expect(table).not.toBeVisible()
    await userEvent.click(screen.getByText('Research details'))
    expect(table).toBeVisible()
    expect(within(table).getAllByRole('row')).toHaveLength(5)
    expect(within(table).getAllByText('5.1%')).toHaveLength(4)
    expect(within(table).getAllByText('2.3%')).toHaveLength(4)
    expect(within(table).getAllByText('Passed')).toHaveLength(4)
    expect(screen.getByText(/Final-check dates do not select settings/)).toBeVisible()
  })

  it.each([
    'not_supported',
    'insufficient',
    'unsupported',
  ] as const)('does not imply symbol and date qualification when the saved receipt is %s', async (status) => {
    const data = findings()
    data.version = 'automatic-hypotheses-v14'
    data.recommendation_checks = {
      version: 'research-recommendation-evidence-v1',
      status: 'not_supported',
      reasons: [],
      sensitivity: {
        version: 'research-recommendation-sensitivity-v1',
        status,
        qualified: false,
        reasons: ['Not enough development signals for separate symbol groups.'],
        scenarios: [],
      },
    }
    render(<AutomaticResearchFindings findings={data} />)
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.getByText(/These checks did not qualify stronger settings/)).toBeVisible()
    expect(
      screen.getByText('Not enough development signals for separate symbol groups.')
    ).toBeVisible()
    expect(
      screen.queryByRole('table', { name: 'Symbol and date comparison' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText(/settings held up across separate symbol groups/)
    ).not.toBeInTheDocument()
  })

  it.each([
    'automatic-hypotheses-v13',
    'automatic-hypotheses-v14',
  ])('does not invent symbol and date checks for %s without a saved receipt', async (version) => {
    const data = findings()
    data.version = version
    render(<AutomaticResearchFindings findings={data} />)
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.queryByRole('region', { name: 'Symbol and date checks' })).not.toBeInTheDocument()
  })
  it('explains when earlier checks avoided unnecessary symbol and date reruns', async () => {
    const data = findings()
    data.version = 'automatic-hypotheses-v14'
    data.recommendation_checks = {
      version: 'research-recommendation-evidence-v1',
      status: 'not_supported',
      reasons: [],
      sensitivity: {
        version: 'research-recommendation-sensitivity-v1',
        status: 'not_run',
        qualified: false,
        reasons: [],
        scenarios: [],
      },
    }
    render(<AutomaticResearchFindings findings={data} />)
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.getByText(/additional reruns were not needed/)).toBeVisible()
    expect(
      screen.queryByRole('table', { name: 'Symbol and date comparison' })
    ).not.toBeInTheDocument()
  })
  it('does not label a profitable rerun passed when native support is insufficient', async () => {
    const data = findings()
    data.version = 'automatic-hypotheses-v14'
    data.recommendation_checks = {
      version: 'research-recommendation-evidence-v1',
      status: 'not_supported',
      reasons: [],
      sensitivity: {
        version: 'research-recommendation-sensitivity-v1',
        status: 'insufficient',
        qualified: false,
        reasons: ['sens_symbols_a: insufficient native trade/date support'],
        scenarios: [
          {
            name: 'sens_symbols_a',
            axis: 'symbols',
            from: '2026-01-05',
            to: '2026-06-30',
            symbols: ['AAA', 'BBB'],
            candidate_return_pct: 5.1,
            baseline_return_pct: 2.3,
            candidate_max_drawdown_pct: 1.2,
            return_minus_drawdown_edge_pct: 2.1,
            passes: true,
            support_sufficient: false,
          },
        ],
      },
    }
    render(<AutomaticResearchFindings findings={data} />)
    await userEvent.click(screen.getByText('Research details'))
    const table = screen.getByRole('table', { name: 'Symbol and date comparison' })
    expect(within(table).getByText('Symbol group A')).toBeVisible()
    expect(within(table).getByText('Not supported')).toBeVisible()
    expect(within(table).queryByText('Passed')).not.toBeInTheDocument()
    expect(screen.getByText('Symbol group A: insufficient native trade/date support')).toBeVisible()
  })
  it('keeps qualified uncertainty evidence in research details without a future-profit claim', () => {
    const data = findings()
    data.version = 'automatic-hypotheses-v12'
    data.recommendation_checks = {
      version: 'research-recommendation-evidence-v1',
      status: 'supported',
      reasons: [],
      uncertainty: { ranges: [{ block_sessions: 5, lower_bps: 1, upper_bps: 3 }] },
    }
    render(<AutomaticResearchFindings findings={data} />)
    expect(
      screen.getByRole('region', {
        name: 'Recommendation evidence',
        hidden: true,
      })
    ).not.toBeVisible()
    expect(
      screen.getByText('Daily return, higher-cost and nearby-setting checks passed.')
    ).toBeInTheDocument()
    expect(screen.getByText(/not a probability of future profit/)).toBeInTheDocument()
    expect(screen.getByText(/5-session blocks: 1 to 3 basis points per day/)).toBeInTheDocument()
  })

  it.each([
    'research-execution-findings-v1',
    'research-execution-findings-v4',
    'research-execution-findings-v5',
  ] as const)('keeps matched original-sizing diagnostics inside development details for %s', async (version) => {
    const data = findings()
    data.version =
      version === 'research-execution-findings-v5'
        ? 'automatic-hypotheses-v16'
        : 'automatic-hypotheses-v10'
    data.execution_research = {
      version,
      ...(version === 'research-execution-findings-v5'
        ? { selected_allocation_mode: 'prior60_cluster_inverse_vol' as const }
        : {}),
      feature_id: 'sealed-feature',
      policy: 'Development only',
      risk_limits: {
        version: 'research-risk-limits-v1',
        max_gross_exposure_pct: 100,
        max_symbol_exposure_pct: 25,
        max_open_positions: 10,
        max_trade_risk_pct: 1,
        max_total_stop_risk_pct: 5,
      },
      sizing_controls: (['fit1', 'fit2', 'fit3', 'search'] as const).map((period) => ({
        version: 'research-sizing-control-v1',
        role: 'matched_original_sizing_development',
        period,
        selected_config_id: 'selected',
        control_config_id: 'control',
        strategies: [],
        sizing_changed: true,
        selected_summary: { net_return_pct: 4.2 },
        summary: { net_return_pct: 3.1 },
        score_delta: 1.1,
        policy: 'Same filters and exits',
      })),
    }
    render(<AutomaticResearchFindings findings={data} />)
    const table = screen.getByRole('table', {
      name: 'Original-sizing development comparison',
      hidden: true,
    })
    expect(table).not.toBeVisible()
    await userEvent.click(screen.getByText('Research details'))
    expect(table).toBeVisible()
    if (version === 'research-execution-findings-v5')
      expect(screen.getByText(/Portfolio allocation: Cluster inverse volatility/)).toBeVisible()
    expect(within(table).getAllByText('4.2%')).toHaveLength(4)
    expect(within(table).getAllByText('3.1%')).toHaveLength(4)
    expect(within(table).getByText('Final development fit')).toBeVisible()
    expect(within(table).queryByText('Final check')).not.toBeInTheDocument()
  })
  it('leaves legacy findings unchanged when account histories were not recorded', async () => {
    render(<AutomaticResearchFindings findings={findings()} />)
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.queryByText(/development account histor/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/final check kept separate/i)).not.toBeInTheDocument()
  })

  it('keeps retained history information collapsed and distinguishes the untouched final check', async () => {
    const evidence: DevelopmentEvidenceMetadata = {
      version: 'research-development-evidence-v1',
      status: 'complete',
      entries: Object.fromEntries(
        Array.from({ length: 82 }, (_, index) => [
          `history-${index}`,
          { status: 'retained' as const },
        ])
      ),
      reserved_final_excluded: true,
      marks: 24000,
      bytes: 5000000,
    }
    render(<AutomaticResearchFindings findings={findings()} developmentEvidence={evidence} />)
    const summary = screen.getByText(
      '82 development account histories saved · final check kept separate'
    )
    expect(summary).not.toBeVisible()
    await userEvent.click(screen.getByText('Research details'))
    expect(summary).toBeVisible()
    expect(screen.queryByText(/24,000|5,000,000|statistical confidence/i)).not.toBeInTheDocument()
  })

  it('counts only retained histories and makes a partial collection explicit', async () => {
    render(
      <AutomaticResearchFindings
        findings={findings()}
        developmentEvidence={{
          version: 'research-development-evidence-v1',
          status: 'partial',
          entries: {
            kept: { status: 'retained' },
            absent: { status: 'unavailable' },
          },
          reserved_final_excluded: true,
          marks: 24,
          bytes: 1000,
        }}
      />
    )
    await userEvent.click(screen.getByText('Research details'))
    expect(
      screen.getByText(
        '1 development account history saved · some histories unavailable · final check kept separate'
      )
    ).toBeVisible()
    expect(screen.queryByText(/complete history|statistical confidence/i)).not.toBeInTheDocument()
  })

  it('reports unavailable histories without claiming completion or final-period separation', async () => {
    render(
      <AutomaticResearchFindings
        findings={findings()}
        developmentEvidence={{
          version: 'research-development-evidence-v1',
          status: 'unavailable',
          entries: {},
          reserved_final_excluded: false,
          marks: 0,
          bytes: 0,
        }}
      />
    )
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.getByText('Development account histories unavailable')).toBeVisible()
    expect(
      screen.queryByText(/account histories saved|final check kept separate/i)
    ).not.toBeInTheDocument()
  })

  it('labels an in-progress collection without calling it complete', async () => {
    render(
      <AutomaticResearchFindings
        findings={findings()}
        developmentEvidence={{
          version: 'research-development-evidence-v1',
          status: 'collecting',
          entries: { kept: { status: 'retained' } },
          reserved_final_excluded: false,
          marks: 24,
          bytes: 1000,
        }}
      />
    )
    await userEvent.click(screen.getByText('Research details'))
    expect(
      screen.getByText('1 development account history saved · collection in progress')
    ).toBeVisible()
  })

  it('shows selected timing beside the saved exits without another disclosure', () => {
    const data = findings()
    data.version = 'automatic-hypotheses-v7'
    render(
      <AutomaticResearchFindings
        findings={data}
        selectedSettings={[
          {
            id: 'scanner',
            name: 'Scanner',
            allocation_pct: 100,
            config: {
              ...researchDefaults,
              trade_horizon: 'intraday',
              hold_minutes: 60,
              entry_time: '09:30',
              exit_time: '15:15',
            },
          },
        ]}
      />
    )
    const settings = screen.getByText(/Enter from 09:30 IST/)
    expect(settings).toHaveTextContent('Exit by 15:15 IST')
    expect(settings).toBeVisible()
  })
  it.each([
    false,
    true,
  ])('shows the frozen selected exits directly, with trailing enabled=%s', (enabled) => {
    const data = findings()
    data.version = 'automatic-hypotheses-v6'
    render(
      <AutomaticResearchFindings
        findings={data}
        selectedSettings={[
          {
            id: 'scanner',
            name: 'Scanner',
            allocation_pct: 100,
            config: {
              ...researchDefaults,
              target_pct: 5.25,
              stop_pct: 2,
              hold_sessions: 2,
              trailing_enabled: enabled,
              trailing_pct: 3.5,
            },
          },
        ]}
      />
    )
    const settings = screen.getByText(/TP 5.25% · SL 2%/)
    expect(settings).toHaveTextContent(
      `TP 5.25% · SL 2% · Up to 2 sessions · ${enabled ? 'Trail 3.5%' : 'Trail off'}`
    )
    expect(settings).toBeVisible()
  })
  it.each([
    ['automatic-trade-management-v1', 'Exits and holding periods'],
    ['automatic-hypotheses-v4', 'Smart research'],
    ['automatic-hypotheses-v5', 'Stock and market research'],
    ['automatic-hypotheses-v6', 'Stock, market and trailing stops'],
    ['automatic-hypotheses-v7', 'Stock, market and trade timing'],
    ['automatic-hypotheses-v8', 'Walk-forward research'],
    ['automatic-hypotheses-v9', 'Expanded automatic research'],
    ['automatic-hypotheses-v10', 'Execution-aware research'],
    ['automatic-hypotheses-v11', 'Portfolio entry research'],
    ['automatic-future-v99', 'Automatic research'],
  ])('keeps the saved %s scope in the collapsed research details', async (version, label) => {
    const data = findings()
    data.version = version
    render(<AutomaticResearchFindings findings={data} />)
    const details = screen.getByText('Research details').closest('details') as HTMLElement
    expect(within(details).getByText(label)).not.toBeVisible()
    await userEvent.click(screen.getByText('Research details'))
    expect(within(details).getByText(label)).toBeVisible()
  })

  it('labels a sampled stability challenge without claiming all nearby settings were tested', async () => {
    const data = findings()
    data.version = 'automatic-trade-management-v2'
    data.checks[0].stability = {
      status: 'stable',
      tested: 8,
      passed: 6,
      pass_fraction: 0.75,
      available: 20,
      truncated: true,
    }
    render(<AutomaticResearchFindings findings={data} />)
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.getByText('Passed later-date, cost and nearby-setting checks')).toBeVisible()
    expect(screen.getByText('Nearby settings: 6 of 8 passed · sampled subset')).toBeVisible()
  })
  it('compares selected and original rules using the final period, with research details collapsed', async () => {
    render(<AutomaticResearchFindings findings={findings()} />)
    expect(screen.getByText('Automatic research')).toBeVisible()
    expect(screen.queryByText('Exploratory research')).not.toBeInTheDocument()
    expect(
      screen.getByRole('heading', {
        name: 'The improvement held up on later dates',
      })
    ).toBeVisible()
    const table = screen.getByRole('table', {
      name: 'Final period comparison',
    })
    expect(
      within(table).getByRole('row', {
        name: 'Original settings 0.9% 1.2% 30',
      })
    ).toBeVisible()
    expect(within(table).getByRole('row', { name: 'Trial 9 2.8% 0.4% 26' })).toBeVisible()
    expect(screen.getByText(/Final check · 1 Aug/)).toHaveTextContent('10 Sept 2026')
    expect(screen.getByText(/50 proposals/)).not.toBeVisible()
    await userEvent.click(screen.getByText('Research details'))
    expect(screen.getByText(/50 proposals · 64 simulations · 3 finalists/)).toBeVisible()
    expect(screen.getByText(/Not included in this run: Indicators, Market regimes/)).toBeVisible()
  })

  it('keeps a baseline fallback honest without presenting it as a new winning trial', () => {
    const data = findings()
    data.selected_is_baseline = true
    data.selected_config_id = 'baseline'
    data.status = 'not_supported'
    data.headline = 'No reliable improvement over your original settings'
    render(<AutomaticResearchFindings findings={data} />)
    const table = screen.getByRole('table', {
      name: 'Final period comparison',
    })
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    expect(within(table).queryByText('Trial 9')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: data.headline })).toBeVisible()
  })

  it('shows inconclusive evidence and missing values without inventing zero or confidence', () => {
    const data = findings()
    data.status = 'inconclusive'
    data.headline = 'More signals are needed for the final check'
    data.final!.summary = {
      net_return_pct: null,
      max_drawdown_pct: Number.NaN,
      closed_trades: 0,
    }
    render(<AutomaticResearchFindings findings={data} />)
    const row = screen.getByRole('row', { name: 'Trial 9 — — 0' })
    expect(row).toBeVisible()
    expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument()
  })

  it('does not show a final comparison when no final calculation exists', () => {
    const data = findings()
    data.final = null
    render(<AutomaticResearchFindings findings={data} />)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('has accessible semantics with expanded details', async () => {
    const { container } = render(<AutomaticResearchFindings findings={findings()} />)
    await userEvent.click(screen.getByText('Research details'))
    expect((await axe(container)).violations).toEqual([])
  })
})

it('does not claim later validation for an exploratory-only smart result', async () => {
  const data = findings()
  data.version = 'automatic-hypotheses-v2'
  data.recipe.validation_mode = 'exploratory_only'
  data.final = null
  data.checks = []
  data.status = 'inconclusive'
  render(<AutomaticResearchFindings findings={data} />)
  expect(screen.getByText('Best settings found on the available research dates.')).toBeVisible()
  expect(
    screen.queryByText('Checked on later dates and with higher trading costs.')
  ).not.toBeInTheDocument()
  expect(screen.queryByRole('table', { name: 'Final period comparison' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByText('Research details'))
  expect(screen.getByText(/No untouched later check/)).toBeVisible()
  expect(screen.queryByText('First check')).not.toBeInTheDocument()
  expect(screen.queryByText('Second check')).not.toBeInTheDocument()
})

function walkForwardFindings(): Findings {
  const data = findings()
  data.version = 'automatic-hypotheses-v8'
  data.walk_forward = {
    version: 'research-walk-forward-findings-v1',
    status: 'supported',
    folds: [1, 2, 3].map((window) => ({
      id: `fold-${window}`,
      fit: { from: '2026-01-01', to: `2026-0${window + 1}-28` },
      test: { from: `2026-0${window + 2}-01`, to: `2026-0${window + 2}-28` },
      selected_config_id: `config-${window}`,
      selected_condition: `Condition ${window}`,
      baseline_summary: { net_return_pct: 0.9, max_drawdown_pct: 1.2 },
      summary: { net_return_pct: 2.8, max_drawdown_pct: 0.4 },
      stress_baseline_summary: { net_return_pct: 0.3 },
      stress_summary: { net_return_pct: 1.5 },
      eligible: true,
      reasons: [],
      training: { proposals: 48, unique_candidates: 42 },
    })),
    final_fit: { from: '2026-01-01', to: '2026-07-31' },
    selection_policy: 'Fit earlier dates, freeze, then check later dates.',
  }
  return data
}

it('keeps fold settings and fresh-cash comparisons in details, preserving the final comparison', async () => {
  render(<AutomaticResearchFindings findings={walkForwardFindings()} />)
  expect(screen.getByText('Refit and checked on 3 later windows.')).toBeVisible()
  const final = screen.getByRole('table', { name: 'Final period comparison' })
  expect(within(final).getByRole('row', { name: 'Trial 9 2.8% 0.4% 26' })).toBeVisible()
  expect(screen.getByText('Window 1')).not.toBeVisible()
  await userEvent.click(screen.getByText('Research details'))
  const table = screen.getByRole('table', {
    name: 'Walk-forward window comparison',
  })
  expect(within(table).getAllByRole('row')).toHaveLength(4)
  expect(within(table).getByText('Condition 1')).toBeVisible()
  expect(within(table).getByText('Condition 3')).toBeVisible()
  expect(within(table).getAllByText('Original 0.3%')).toHaveLength(3)
  expect(screen.getByText(/Accounts restart with the same cash/)).toBeVisible()
  expect(screen.getByText(/Settings were frozen before the reserved final check/)).toBeVisible()
  expect(
    screen.getByText(/detailed report covers the final fit on development dates/)
  ).toBeVisible()
})

it.each([
  'absent',
  'missing-fold',
  'overlap',
  'malformed',
  'unknown-version',
  'missing-training',
] as const)('does not infer completed walk-forward checks for a %s receipt', (kind) => {
  const data = walkForwardFindings()
  if (kind === 'absent') delete data.walk_forward
  else if (kind === 'missing-fold') data.walk_forward!.folds.pop()
  else if (kind === 'overlap') data.walk_forward!.folds[1].fit.to = '2026-06-01'
  else if (kind === 'unknown-version')
    data.walk_forward!.version = 'future-v9' as 'research-walk-forward-findings-v1'
  else if (kind === 'missing-training')
    data.walk_forward!.folds[0].training = undefined as unknown as NonNullable<
      Findings['walk_forward']
    >['folds'][number]['training']
  else
    data.walk_forward = {
      folds: [null, null, null],
    } as unknown as Findings['walk_forward']
  render(<AutomaticResearchFindings findings={data} />)
  expect(screen.queryByText(/Refit and checked on 3 later windows/)).not.toBeInTheDocument()
  expect(
    screen.queryByRole('table', { name: 'Walk-forward window comparison' })
  ).not.toBeInTheDocument()
})

it('does not turn unsuccessful walk-forward checks into a claim of validation', async () => {
  const data = walkForwardFindings()
  data.status = 'not_supported'
  data.headline = 'No reliable improvement over your original settings'
  data.walk_forward!.status = 'not_supported'
  data.walk_forward!.folds[0].eligible = false
  data.walk_forward!.folds[0].reasons = ['Too few closed trades']
  render(<AutomaticResearchFindings findings={data} />)
  expect(screen.getByRole('heading', { name: data.headline })).toBeVisible()
  await userEvent.click(screen.getByText('Research details'))
  expect(screen.getByText('Not supported')).toBeVisible()
  expect(screen.getByText('Too few closed trades')).toBeVisible()
})

it('keeps walk-forward details accessible', async () => {
  const { container } = render(<AutomaticResearchFindings findings={walkForwardFindings()} />)
  await userEvent.click(screen.getByText('Research details'))
  expect((await axe(container)).violations).toEqual([])
})

it('shows the fitted trading rules for each recorded fold inside research details', async () => {
  const data = walkForwardFindings()
  data.walk_forward!.folds[0].selected_settings = [
    {
      id: 'scanner',
      name: 'Scanner',
      allocation_pct: 100,
      config: {
        ...researchDefaults,
        target_pct: 4.25,
        stop_pct: 1.5,
        trade_horizon: 'intraday',
        hold_minutes: 60,
        trailing_enabled: true,
        trailing_pct: 2.5,
        entry_time: '09:30',
        exit_time: '15:15',
      },
    },
  ]
  data.walk_forward!.folds[1].selected_settings = [
    {
      id: 'scanner',
      name: 'Scanner',
      allocation_pct: 100,
      config: {
        ...researchDefaults,
        target_pct: 6,
        stop_pct: 2,
        hold_sessions: 3,
        trailing_enabled: false,
      },
    },
  ]
  render(<AutomaticResearchFindings findings={data} />)
  const firstRules = screen.getByText(/TP 4.25% · SL 1.5%/)
  expect(firstRules).not.toBeVisible()
  await userEvent.click(screen.getByText('Research details'))
  expect(firstRules).toBeVisible()
  expect(firstRules).toHaveTextContent(
    'Up to 60 min · Trail 2.5% · Enter from 09:30 IST · Exit by 15:15 IST'
  )
  const secondRules = screen.getByText(/TP 6% · SL 2%/)
  expect(secondRules).toHaveTextContent('Up to 3 sessions · Trail off')
  expect(secondRules.closest('tr')).toHaveTextContent('Window 2')
  expect(firstRules.closest('tr')).toHaveTextContent('Window 1')
})

it('keeps earlier fold receipts readable without inventing missing fitted trading rules', async () => {
  render(<AutomaticResearchFindings findings={walkForwardFindings()} />)
  await userEvent.click(screen.getByText('Research details'))
  expect(screen.getByRole('table', { name: 'Walk-forward window comparison' })).toBeVisible()
  expect(screen.getByText('config-1')).toBeVisible()
  expect(screen.queryByText(/TP .*SL/)).not.toBeInTheDocument()
})

it('keeps the expanded programme later-window evidence inside research details', async () => {
  const data = walkForwardFindings()
  data.version = 'automatic-hypotheses-v9'
  render(<AutomaticResearchFindings findings={data} />)
  const table = screen.getByRole('table', {
    name: 'Walk-forward window comparison',
    hidden: true,
  })
  expect(table).not.toBeVisible()
  await userEvent.click(screen.getByText('Research details'))
  expect(table).toBeVisible()
  expect(screen.getByText('Refit and checked on 3 later windows.')).toBeVisible()
})
