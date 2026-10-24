import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  type JointEntryConditions,
  type PortfolioCapabilities,
  type PortfolioJob,
  type PortfolioResult,
  portfolioResearch,
} from '@/api/portfolioResearch'
import { researchPartialStudy } from '@/api/researchPartialStudy'
import { researchStudyActivity } from '@/api/researchStudyActivity'
import { researchValidation } from '@/api/researchValidation'
import type { ResearchSource } from '@/api/scannerResearch'
import PortfolioResearch from '@/pages/PortfolioResearch'
import { useAuthStore } from '@/stores/authStore'
import {
  addPortfolioSource,
  applySuggestedSearch,
  defaultRiskLimits,
  freshPortfolioDraft,
  limitsForScope,
  portfolioDraftIssue,
  portfolioEngineIssue,
  portfolioPayload,
  selectPortfolioRun,
  suggestedStrategySearch,
} from './PortfolioBuilder'
import { PortfolioResults, StrategySettings } from './PortfolioResults'
import { ReportCurrency } from './ReportCurrency'

vi.mock('@/api/portfolioResearch', () => ({
  portfolioResearch: {
    capabilities: vi.fn(),
    sources: vi.fn(),
    upload: vi.fn(),
    preflight: vi.fn(),
    submit: vi.fn(),
    job: vi.fn(),
    jobs: vi.fn(),
    cancel: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    rerun: vi.fn(),
    conditionReplay: vi.fn(),
    analysis: vi.fn().mockResolvedValue({ status: 'missing' }),
    prepareAnalysis: vi.fn(),
    analysisExportUrl: (id: string) => `/scanner-research/api/portfolio/jobs/${id}/analysis/export`,
    exportUrl: (id: string) => `/scanner-research/api/jobs/${id}/export`,
  },
}))
vi.mock('@/api/researchStudyActivity', () => ({
  researchStudyActivity: { get: vi.fn() },
}))
vi.mock('@/api/researchPartialStudy', () => ({
  researchPartialStudy: { get: vi.fn() },
}))
vi.mock('@/components/portfolio/PortfolioLineChart', () => ({
  PortfolioLineChart: vi.fn(() => <div>Native portfolio chart</div>),
}))
vi.mock('./ResearchPlot', () => ({
  default: ({ chart }: { chart: { id: string; figure: unknown } }) => (
    <output aria-label={chart.id}>{JSON.stringify(chart.figure)}</output>
  ),
}))
const source = (id: string, interval = 'D'): ResearchSource => ({
  id,
  receipt: {
    input_rows: 4,
    signal_count: 4,
    duplicates_removed: 0,
    date_from: '2026-01-05',
    date_to: '2026-01-08',
    symbol_count: 2,
    warnings: [],
  },
  coverage: {},
  provenance: {
    provider: 'queued-openalgo-history',
    interval,
    exchange: 'NSE',
    adjustment_basis: 'broker',
    calendar_basis: 'native',
    synthetic: false,
  },
})
function draft() {
  return addPortfolioSource(freshPortfolioDraft(), source('a'.repeat(32)), 'Breakout.csv')
}
const account = draft()
const strategy = account.portfolio.strategies[0]
const capabilities: PortfolioCapabilities = {
  max_strategies: 8,
  engines: [
    {
      id: 'vectorbt',
      name: 'VectorBT',
      available: true,
      intervals: ['D', '1m'],
    },
    {
      id: 'nautilus',
      name: 'NautilusTrader',
      available: false,
      intervals: ['D', '1m'],
    },
  ],
  optimizers: [{ id: 'optuna', available: true, samplers: ['tpe', 'grid'] }],
}
const result: PortfolioResult = {
  portfolio: account.portfolio,
  config: { initial_capital: 100000 },
  strategies: [strategy],
  summary: {
    initial_capital: 100000,
    final_equity: 101000,
    net_pnl: 1000,
    net_return_pct: 1,
    max_drawdown_pct: 0.5,
    closed_trades: 3,
    pending_trades: 0,
    skipped_trades: 0,
  },
  equity_curve: [
    {
      date: '2026-01-06',
      timestamp: '2026-01-06T09:16:00+05:30',
      equity: 101000,
      cash: 99000,
      drawdown_pct: 0.5,
    },
  ],
  ledger: [
    {
      strategy_id: strategy.id,
      strategy_name: strategy.name,
      symbol: 'TCS',
      status: 'closed',
      source_row: 2,
      entry_date: '2026-01-06',
      exit_date: '2026-01-07',
      entry_price: 100,
      exit_price: 110,
      quantity: 10,
      pnl: 100,
      reason: 'target; open exit',
    },
  ],
  per_strategy: [
    {
      id: strategy.id,
      name: strategy.name,
      allocation_pct: 100,
      net_pnl: 1000,
      contribution_pct: 1,
      summary: { closed_trades: 3 },
    },
  ],
  source: {
    provider: 'OpenAlgo Historify',
    interval: '1m',
    signal_count: 4,
    strategy_count: 1,
  },
  execution: { engine: 'vectorbt', engine_version: '0.28.5', interval: '1m' },
}
const finished: PortfolioJob = {
  id: 'finished-job',
  kind: 'portfolio_backtest',
  status: 'completed',
  progress: 100,
  created_at: 1,
  specification: { portfolio: account.portfolio },
  result,
}
const running: PortfolioJob = {
  ...finished,
  id: 'running-job',
  result: undefined,
  status: 'running',
  progress: 39,
}
const clients: QueryClient[] = []
function RunLocation() {
  return <output data-testid="run-location">{useLocation().search}</output>
}
function mount(path = '/scanner-research', onLegacyJob?: (id: string) => void) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <PortfolioResearch onLegacyJob={onLegacyJob} />
        <RunLocation />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
function savedDraft(value = draft()) {
  sessionStorage.setItem('portfolio-draft:account', JSON.stringify(value))
}
beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  useAuthStore.setState({ user: null })
  vi.mocked(portfolioResearch.capabilities).mockResolvedValue(capabilities)
  vi.mocked(portfolioResearch.jobs).mockResolvedValue({
    items: [],
    next_cursor: null,
  })
  vi.mocked(portfolioResearch.sources).mockResolvedValue({
    items: [],
    next_offset: null,
  })
  vi.mocked(portfolioResearch.job).mockResolvedValue(running)
  vi.mocked(portfolioResearch.preflight).mockImplementation(async (portfolio) => ({
    portfolio,
    interval: 'D',
    receipt: {
      ...source('a').receipt,
      strategy_count: portfolio.strategies.length,
    },
    versions: {},
  }))
  vi.mocked(portfolioResearch.submit).mockResolvedValue(running)
  vi.mocked(portfolioResearch.upload).mockResolvedValue(source('a'.repeat(32)))
  vi.mocked(researchStudyActivity.get).mockResolvedValue({
    version: 'research-study-activity-v1',
    job_id: running.id,
    job_status: 'failed',
    available: false,
    reason: 'not_recorded',
    executions: [],
    executions_truncated: false,
    counts: {
      recorded: 0,
      running: 0,
      evaluated: 0,
      reused: 0,
      allocation_rejected: 0,
      failed: 0,
      cancelled: 0,
      interrupted: 0,
    },
    rows: [],
    next_before: null,
  })
})
afterEach(() => {
  for (const client of clients) client.clear()
  clients.length = 0
})

describe('native portfolio consumer journey', () => {
  it('selects the broad automatic recipe when Optimize is first chosen, without tuning cash or costs', () => {
    const original = draft()
    const optimized = selectPortfolioRun(original, true)
    expect(optimized.optimizing).toBe(true)
    expect(optimized.portfolio.automatic_research).toEqual({
      version: 'automatic-hypotheses-v16',
      history_policy: 'available_history',
      design_policy: 'automatic_design_v1',
      decision_policy: 'automatic_decision_v1',
      action_policy: 'automatic_actions_v1',
    })
    expect(optimized.portfolio.strategies).toEqual(original.portfolio.strategies)
    expect(optimized.portfolio.capital).toBe(original.portfolio.capital)
    expect(original.portfolio.automatic_research).toBeUndefined()
  })
  it('starts a new priority programme from capped percentage rules without changing saved v10', () => {
    const fixed = draft()
    fixed.portfolio.risk_limits = { ...defaultRiskLimits }
    fixed.portfolio.strategies[0].execution_policy = {
      version: 'research-execution-aware-v1',
      exit_mode: 'percent',
      sizing: 'allocation',
      risk_limits: fixed.portfolio.risk_limits,
    }
    const next = selectPortfolioRun(fixed, true)
    expect(next.portfolio.automatic_research?.version).toBe('automatic-hypotheses-v16')
    expect(next.portfolio.strategies[0].execution_policy).toEqual({
      ...fixed.portfolio.strategies[0].execution_policy,
      version: 'research-execution-aware-v5',
      allocation_mode: 'original',
      capacity_mode: 'prior20_broker_reported',
      correlation_mode: 'prior60_positive_080',
      risk_limits: limitsForScope(fixed.portfolio.risk_limits, 'automatic-hypotheses-v15'),
      entry_priority: 'source',
    })
    expect(fixed.portfolio.strategies[0].execution_policy.version).toBe(
      'research-execution-aware-v1'
    )
    fixed.portfolio.automatic_research = {
      version: 'automatic-hypotheses-v10',
    }
    expect(selectPortfolioRun(fixed, true).portfolio).toEqual(fixed.portfolio)
  })
  it('adds a CSV to a chosen-policy account with fresh trade rules and shared account limits', () => {
    const fixed = draft()
    fixed.portfolio.risk_limits = { ...defaultRiskLimits }
    fixed.portfolio.strategies[0].execution_policy = {
      version: 'research-execution-aware-v2',
      exit_mode: 'atr',
      atr_period: 14,
      atr_stop_multiple: 2,
      atr_target_multiple: 3,
      sizing: 'stop_risk',
      risk_budget_fraction: 0.5,
      entry_priority: 'lower_stop_risk',
      risk_limits: fixed.portfolio.risk_limits,
    }
    const added = addPortfolioSource(fixed, source('b'.repeat(32)), 'Momentum.csv')
    expect(added.portfolio.strategies[1].execution_policy).toEqual({
      version: 'research-execution-aware-v2',
      exit_mode: 'percent',
      sizing: 'allocation',
      entry_priority: 'lower_stop_risk',
      risk_limits: fixed.portfolio.risk_limits,
    })
    expect(added.portfolio.strategies[0].execution_policy).toEqual(
      fixed.portfolio.strategies[0].execution_policy
    )
    expect(selectPortfolioRun(fixed, true).portfolio.automatic_research).toBeUndefined()
  })
  it.each([
    'automatic-hypotheses-v2',
    'automatic-hypotheses-v3',
    'automatic-hypotheses-v4',
    'automatic-hypotheses-v5',
    'automatic-hypotheses-v6',
    'automatic-hypotheses-v7',
    'automatic-hypotheses-v8',
    'automatic-hypotheses-v9',
    'automatic-hypotheses-v10',
    'automatic-hypotheses-v11',
    'automatic-hypotheses-v12',
    'automatic-hypotheses-v13',
    'automatic-hypotheses-v14',
    'automatic-hypotheses-v15',
  ] as const)('keeps the saved %s recipe when optimizing again', (version) => {
    const saved = draft()
    saved.portfolio.automatic_research = { version }
    expect(selectPortfolioRun(saved, true).portfolio.automatic_research).toEqual({ version })
  })
  it('preserves saved scopes, custom ranges and chosen entry conditions when switching run type', () => {
    const saved = draft()
    saved.portfolio.automatic_research = { version: 'automatic-hypotheses-v6' }
    expect(selectPortfolioRun(saved, true).portfolio.automatic_research).toEqual(
      saved.portfolio.automatic_research
    )
    const custom = applySuggestedSearch(draft())
    expect(selectPortfolioRun(custom, true).portfolio.automatic_research).toBeUndefined()
    const conditioned = draft()
    conditioned.portfolio.entry_conditions = {
      version: 'research-entry-conditions-v1',
      rules: [],
    }
    expect(selectPortfolioRun(conditioned, true).portfolio.automatic_research).toBeUndefined()
  })

  it('defaults to execution-aware research and preserves an explicitly selected prior recipe', async () => {
    const original = draft()
    savedDraft({ ...original, optimizing: true })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic research' }))
    expect(screen.getByLabelText('Research scope')).toHaveValue('automatic-hypotheses-v16')
    await userEvent.click(screen.getByText('Advanced research options'))
    await userEvent.selectOptions(
      screen.getByLabelText('Research scope'),
      'automatic-hypotheses-v6'
    )
    expect(
      screen.getByText(/Tests stock signals, Nifty trend or volatility, and exits together/)
    ).toHaveTextContent('320 calculations')
    expect(
      screen.getByText(/Tests stock signals, Nifty trend or volatility, and exits together/)
    ).toHaveTextContent('trailing stops on or off')
    await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(sent.automatic_research).toEqual({
      version: 'automatic-hypotheses-v6',
    })
    expect(sent.capital).toBe(original.portfolio.capital)
    expect(sent.strategies[0].config).toEqual(original.portfolio.strategies[0].config)
    expect(sent.strategies[0].allocation_pct).toBe(original.portfolio.strategies[0].allocation_pct)
    expect(sent.optimization).toBeUndefined()
    expect(sent.validation).toBeUndefined()
  })

  it('does not upgrade a saved automatic recipe when its active approach is clicked', async () => {
    const original = draft()
    original.optimizing = true
    original.portfolio.automatic_research = {
      version: 'automatic-hypotheses-v4',
    }
    savedDraft(original)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic research' }))
    expect(screen.getByLabelText('Research scope')).toHaveValue('automatic-hypotheses-v4')
    await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    expect(vi.mocked(portfolioResearch.submit).mock.calls[0][0].automatic_research).toEqual({
      version: 'automatic-hypotheses-v4',
    })
  })
  it('rejects a persisted VectorBT-only research scope on Nautilus without changing its recipe', () => {
    const original = draft()
    original.optimizing = true
    original.portfolio.engine = 'nautilus'
    original.portfolio.automatic_research = {
      version: 'automatic-hypotheses-v7',
    }
    expect(portfolioEngineIssue(original)).toMatch(/need VectorBT/)
    expect(original.portfolio.automatic_research.version).toBe('automatic-hypotheses-v7')
  })

  it('retains the prior stock-and-market recipe when a saved study is reused', () => {
    const original = draft()
    original.optimizing = true
    original.portfolio.automatic_research = {
      version: 'automatic-hypotheses-v5',
    }
    expect(selectPortfolioRun(original, true).portfolio.automatic_research).toEqual({
      version: 'automatic-hypotheses-v5',
    })
    original.portfolio.engine = 'nautilus'
    original.portfolio.automatic_research = {
      version: 'automatic-hypotheses-v6',
    }
    expect(portfolioEngineIssue(original)).toMatch(/need VectorBT/)
  })

  it.each([
    'original',
    'prior60_cluster_inverse_vol',
  ] as const)('shows saved portfolio allocation %s in Settings', (allocation_mode) => {
    render(
      <StrategySettings
        strategy={{
          ...strategy,
          execution_policy: {
            version: 'research-execution-aware-v5',
            exit_mode: 'percent',
            sizing: 'allocation',
            capacity_mode: 'disabled',
            correlation_mode: 'prior60_positive_080',
            allocation_mode,
            risk_limits: limitsForScope(defaultRiskLimits, 'automatic-hypotheses-v16'),
          },
        }}
      />
    )
    expect(screen.getByText('Portfolio allocation').closest('div')).toHaveTextContent(
      allocation_mode === 'original'
        ? 'Original allocation'
        : 'Cluster inverse volatility · new entries'
    )
  })
  it('includes the saved stock entry rule alongside exits in report settings', () => {
    render(
      <StrategySettings
        strategy={{
          ...strategy,
          entry_condition: {
            version: 'research-stock-entry-conditions-v2',
            hypothesis: {
              id: 'rsi_14_gt60',
              family: 'momentum',
              label: 'RSI 14 above 60',
              lookback: 14,
              threshold: 60,
            },
          },
        }}
      />
    )
    expect(screen.getByText('Entry condition').closest('div')).toHaveTextContent('RSI 14 above 60')
    expect(screen.getByText('Profit target')).toBeVisible()
  })
  it('shows a readable saved market rule and omits entry rules from unfiltered settings', () => {
    const view = render(
      <StrategySettings
        strategy={{
          ...strategy,
          entry_condition: {
            dimension: 'trend',
            regime: 'up',
            recipe_id: 'saved-recipe',
          },
        }}
      />
    )
    expect(screen.getByText('Entry condition').closest('div')).toHaveTextContent('Rising market')
    view.rerender(<StrategySettings strategy={strategy} />)
    expect(screen.queryByText('Entry condition')).not.toBeInTheDocument()
  })

  it('reopens joint rules in Setup and submits both conditions unchanged', async () => {
    const conditions: JointEntryConditions = {
      version: 'research-joint-entry-conditions-v1',
      hypothesis: {
        id: 'trend_up__trend_up',
        family: 'trend:trend_up',
        label: 'Above SMA 50 · Rising Nifty trend',
        stock_hypothesis: {
          id: 'trend_up',
          family: 'trend',
          label: 'Above SMA 50',
          lookback: 50,
          threshold: null,
        },
        market: { dimension: 'trend', regime: 'up' },
      },
    }
    const original = draft()
    original.portfolio.entry_conditions = conditions
    original.portfolio.strategies[0].entry_condition = conditions
    savedDraft(original)
    mount()
    expect(await screen.findByText('Above SMA 50 · Rising Nifty trend entries only.')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(sent.entry_conditions).toEqual(conditions)
    expect(sent.strategies[0].entry_condition).toEqual(conditions)
  })

  it('runs automatic research without manual axes and keeps financial inputs unchanged', async () => {
    const original = draft()
    original.optimizing = true
    original.portfolio.automatic_research = {
      version: 'automatic-trade-management-v1',
    }
    original.portfolio.validation = { train_pct: 80, mode: 'reserve' }
    original.optimization.trials = 123
    original.portfolio.strategies[0].config.cost_bps = 7
    savedDraft(original)
    mount()
    const start = await screen.findByRole('button', {
      name: 'Optimize automatically',
    })
    expect(screen.queryByRole('region', { name: 'Optimization ranges' })).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton', { name: 'Trials' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Reserve a later period')).not.toBeInTheDocument()
    expect(portfolioDraftIssue(original)).toBeNull()
    await userEvent.click(start)
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(sent.automatic_research).toEqual({
      version: 'automatic-trade-management-v1',
    })
    expect(sent.optimization).toBeUndefined()
    expect(sent.validation).toBeUndefined()
    expect(sent.strategies[0].search).toEqual({})
    expect(sent.capital).toBe(original.portfolio.capital)
    expect(sent.strategies[0].config).toEqual({
      ...original.portfolio.strategies[0].config,
      initial_capital: original.portfolio.capital,
    })
    expect(sent.strategies[0].allocation_pct).toBe(original.portfolio.strategies[0].allocation_pct)
  })

  it('keeps saved custom ranges and reservation when switching research approaches', async () => {
    const original = applySuggestedSearch({ ...draft(), optimizing: true })
    original.optimization.trials = 61
    original.portfolio.validation = { train_pct: 80, mode: 'reserve' }
    savedDraft(original)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic research' }))
    expect(screen.getByRole('button', { name: 'Optimize automatically' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Settings for Breakout' }))
    expect(screen.queryByLabelText('Optimize Stop loss (%)')).not.toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByRole('button', { name: 'Custom search' }))
    expect(screen.getByRole('spinbutton', { name: 'Trials' })).toHaveValue(61)
    expect(screen.getByLabelText('Reserve a later period')).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Run optimization' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(sent.automatic_research).toBeUndefined()
    expect(sent.strategies[0].search).toEqual(original.portfolio.strategies[0].search)
    expect(sent.optimization?.trials).toBe(61)
    expect(sent.validation).toEqual(original.portfolio.validation)
  })

  it('starts joint stock-and-exit research with one action and fixed account risk', async () => {
    const original = draft()
    savedDraft({ ...original, optimizing: true })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic research' }))
    expect(screen.getByLabelText('Research scope')).toHaveValue('automatic-hypotheses-v16')
    expect(screen.getByText(/Tests percentage and ATR exits/)).not.toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(sent.automatic_research).toEqual({
      version: 'automatic-hypotheses-v16',
      history_policy: 'available_history',
      design_policy: 'automatic_design_v1',
      decision_policy: 'automatic_decision_v1',
      action_policy: 'automatic_actions_v1',
    })
    expect(sent.capital).toBe(original.portfolio.capital)
    expect(sent.strategies[0].config).toEqual(original.portfolio.strategies[0].config)
    expect(sent.strategies[0].allocation_pct).toBe(original.portfolio.strategies[0].allocation_pct)
    expect(sent.optimization).toBeUndefined()
    expect(sent.validation).toBeUndefined()
  })

  it('shows the additional repeat budget only for the latest automatic scope', async () => {
    savedDraft({ ...draft(), optimizing: true })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic research' }))
    await userEvent.click(screen.getByText('Advanced research options'))
    expect(screen.getByText(/Up to 512 calculations/)).toBeVisible()
    expect(screen.getByText(/408 proposals in total/)).toBeVisible()
    await userEvent.selectOptions(
      screen.getByLabelText('Research scope'),
      'automatic-hypotheses-v14'
    )
    expect(screen.getByText(/Up to 384 calculations/)).toBeVisible()
    expect(screen.queryByText(/408 proposals in total/)).not.toBeInTheDocument()
  })

  it('summarises limits, reveals their controls on demand and sends exactly those limits', async () => {
    savedDraft({ ...draft(), optimizing: true })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic research' }))
    expect(screen.getByText('100% exposure · 25% per symbol')).toBeVisible()
    expect(screen.getByLabelText('Gross exposure %')).not.toBeVisible()
    await userEvent.click(screen.getByText('Portfolio limits', { exact: true }))
    expect(screen.getByLabelText('Gross exposure %')).toBeVisible()
    expect(screen.getByLabelText('Gross exposure %')).toHaveValue(100)
    expect(screen.getByLabelText('Per symbol %')).toHaveValue(25)
    expect(screen.getByLabelText('Risk per trade %')).toHaveValue(1)
    expect(screen.getByLabelText('Daily buy budget %')).toHaveValue(100)
    fireEvent.change(screen.getByLabelText('Daily buy budget %'), { target: { value: '40' } })
    fireEvent.change(screen.getByLabelText('Open positions'), {
      target: { value: '4' },
    })
    await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(sent.risk_limits).toEqual({
      ...limitsForScope(defaultRiskLimits, 'automatic-hypotheses-v15'),
      max_open_positions: 4,
      max_daily_buy_turnover_pct: 40,
    })
    expect(sent.automatic_research?.version).toBe('automatic-hypotheses-v16')
  })

  it('rejects invalid caps and keeps selected execution rules in fixed requests', () => {
    const current = draft()
    current.portfolio.risk_limits = {
      ...defaultRiskLimits,
      max_open_positions: 0,
    }
    expect(portfolioDraftIssue(current)).toMatch(/open positions/)
    current.portfolio.risk_limits.max_open_positions = 4
    current.portfolio.strategies[0].execution_policy = {
      version: 'research-execution-aware-v1',
      exit_mode: 'atr',
      sizing: 'stop_risk',
      atr_period: 14,
      atr_stop_multiple: 2,
      atr_target_multiple: 3,
      risk_limits: current.portfolio.risk_limits,
    }
    const fixed = portfolioPayload({ ...current, optimizing: false })
    expect(fixed.strategies[0].execution_policy).toEqual(
      current.portfolio.strategies[0].execution_policy
    )
    expect(fixed.risk_limits).toEqual(current.portfolio.risk_limits)
    expect(
      portfolioEngineIssue({
        ...current,
        portfolio: { ...current.portfolio, engine: 'nautilus' },
      })
    ).toMatch(/need VectorBT/)
  })

  it('keeps correlation limits when broker-volume modeling is explicitly turned off', async () => {
    savedDraft({ ...draft(), optimizing: true })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic research' }))
    expect(screen.getByLabelText('Daily volume participation %')).toHaveValue(1)
    expect(screen.getByLabelText('Correlated exposure %')).toHaveValue(25)
    await userEvent.click(screen.getByLabelText('Use broker-volume participation limit'))
    await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(sent.strategies.every((row) => row.execution_policy?.capacity_mode === 'disabled')).toBe(
      true
    )
    expect(
      sent.strategies.every(
        (row) => row.execution_policy?.correlation_mode === 'prior60_positive_080'
      )
    ).toBe(true)
    expect(sent.risk_limits?.max_correlated_exposure_pct).toBe(25)
  })

  it('preserves both account-wide modes when another CSV is added', () => {
    const current = draft()
    const limits = limitsForScope(defaultRiskLimits, 'automatic-hypotheses-v15')
    current.portfolio.risk_limits = limits
    current.portfolio.strategies[0].execution_policy = {
      version: 'research-execution-aware-v4',
      exit_mode: 'percent',
      sizing: 'allocation',
      entry_priority: 'source',
      risk_limits: limits,
      capacity_mode: 'disabled',
      correlation_mode: 'prior60_positive_080',
    }
    const source = Object.values(current.sources)[0]
    const added = addPortfolioSource(current, source, 'Another.csv')
    expect(added.portfolio.strategies.at(-1)?.execution_policy).toEqual(
      current.portfolio.strategies[0].execution_policy
    )
    const invalid = structuredClone(current)
    invalid.portfolio.risk_limits!.max_daily_volume_participation_pct = 11
    expect(portfolioDraftIssue(invalid)).toMatch(/volume participation/)
    invalid.portfolio.risk_limits!.max_daily_volume_participation_pct = 1
    invalid.portfolio.risk_limits!.max_correlated_exposure_pct = 0
    expect(portfolioDraftIssue(invalid)).toMatch(/correlated exposure/)
  })

  it.each([
    'automatic-hypotheses-v13',
    'automatic-hypotheses-v14',
  ] as const)('retains explicit correlation-only modes when upgrading saved %s to the latest search', async (version) => {
    const saved = draft()
    saved.portfolio.automatic_research = { version }
    saved.portfolio.risk_limits = limitsForScope(defaultRiskLimits, version)
    saved.portfolio.strategies[0].execution_policy = {
      version: 'research-execution-aware-v4',
      exit_mode: 'percent',
      sizing: 'allocation',
      entry_priority: 'source',
      risk_limits: saved.portfolio.risk_limits,
      capacity_mode: 'disabled',
      correlation_mode: 'prior60_positive_080',
    }
    savedDraft({ ...saved, optimizing: true })
    mount()
    await userEvent.click(await screen.findByText('Advanced research options'))
    expect(screen.getByLabelText('Research scope')).toHaveValue(version)
    await userEvent.selectOptions(
      screen.getByLabelText('Research scope'),
      'automatic-hypotheses-v16'
    )
    expect(screen.getByLabelText('Use broker-volume participation limit')).not.toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(sent.automatic_research?.version).toBe('automatic-hypotheses-v16')
    expect(sent.strategies[0].execution_policy?.allocation_mode).toBe('original')
    expect(sent.risk_limits).toEqual(saved.portfolio.risk_limits)
    expect(sent.strategies[0].execution_policy?.capacity_mode).toBe('disabled')
    expect(sent.strategies[0].execution_policy?.correlation_mode).toBe('prior60_positive_080')
  })

  it('submits the bounded market-condition recipe only when explicitly selected', async () => {
    savedDraft({ ...draft(), optimizing: true })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic research' }))
    await userEvent.click(screen.getByText('Advanced research options'))
    await userEvent.selectOptions(
      screen.getByLabelText('Research scope'),
      'automatic-entry-conditions-v1'
    )
    expect(screen.getByText(/Compares no filter, rising Nifty trend/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    expect(vi.mocked(portfolioResearch.submit).mock.calls[0][0].automatic_research).toEqual({
      version: 'automatic-entry-conditions-v1',
    })
  })

  it('submits stability-checked research without custom ranges or larger hidden budgets', async () => {
    savedDraft({ ...draft(), optimizing: true })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic research' }))
    await userEvent.click(screen.getByText('Advanced research options'))
    await userEvent.selectOptions(
      screen.getByLabelText('Research scope'),
      'automatic-trade-management-v2'
    )
    expect(screen.getByText(/Challenges nearby settings/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(sent.automatic_research).toEqual({
      version: 'automatic-trade-management-v2',
    })
    expect(sent.validation).toBeUndefined()
    expect(sent.optimization).toBeUndefined()
  })

  it('shows fixed entry rules and removes only a removed strategy’s rule', async () => {
    const initial = addPortfolioSource(draft(), source('b'.repeat(32)), 'Second.csv')
    initial.optimizing = true
    initial.portfolio.entry_conditions = {
      version: 'research-entry-conditions-v1',
      recipe: { id: 'saved-recipe' },
      rules: initial.portfolio.strategies.map((item) => ({
        strategy_id: item.id,
        dimension: 'trend',
        regime: 'up',
      })),
    }
    savedDraft(initial)
    mount()
    expect(await screen.findByRole('region', { name: 'Saved entry conditions' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Automatic research' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Remove Breakout' }))
    const retained = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(retained.portfolio.entry_conditions.rules).toEqual([
      initial.portfolio.entry_conditions.rules[1],
    ])
    await userEvent.click(screen.getByRole('button', { name: 'Remove Second' }))
    expect(
      JSON.parse(sessionStorage.getItem('portfolio-draft:account')!).portfolio.entry_conditions
    ).toBeUndefined()
  })

  it('removes the automatic recipe from a backtest while preserving the saved draft', () => {
    const original = draft()
    original.portfolio.automatic_research = {
      version: 'automatic-trade-management-v1',
    }
    const payload = portfolioPayload(original)
    expect(payload.automatic_research).toBeUndefined()
    expect(payload.optimization).toBeUndefined()
    expect(original.portfolio.automatic_research).toBeDefined()
  })

  it.each([
    ['automatic-hypotheses-v10', 'research-execution-aware-v1'],
    ['automatic-hypotheses-v11', 'research-execution-aware-v2'],
    ['automatic-hypotheses-v12', 'research-execution-aware-v3'],
    ['automatic-hypotheses-v13', 'research-execution-aware-v4'],
    ['automatic-hypotheses-v14', 'research-execution-aware-v4'],
    ['automatic-hypotheses-v15', 'research-execution-aware-v4'],
    ['automatic-hypotheses-v16', 'research-execution-aware-v5'],
  ] as const)('keeps the implicit %s controls when running its fixed reference', (scope, version) => {
    const original = draft()
    original.portfolio.automatic_research = { version: scope }
    original.portfolio.risk_limits = {
      ...limitsForScope(defaultRiskLimits, scope),
      max_open_positions: 4,
      max_symbol_exposure_pct: 17,
    }
    original.portfolio.strategies[0].config = {
      ...original.portfolio.strategies[0].config,
      target_pct: 40,
      stop_pct: 30,
      hold_sessions: 45,
      trailing_enabled: true,
      trailing_pct: 5,
    }
    const before = structuredClone(original)
    const fixed = portfolioPayload({ ...original, optimizing: false })
    expect(fixed.automatic_research).toBeUndefined()
    expect(fixed.optimization).toBeUndefined()
    expect(fixed.risk_limits).toEqual(original.portfolio.risk_limits)
    expect(fixed.strategies[0].execution_policy).toEqual({
      version,
      exit_mode: 'percent',
      sizing: 'allocation',
      risk_limits: original.portfolio.risk_limits,
      ...(scope !== 'automatic-hypotheses-v10' ? { entry_priority: 'source' } : {}),
      ...([
        'automatic-hypotheses-v13',
        'automatic-hypotheses-v14',
        'automatic-hypotheses-v15',
        'automatic-hypotheses-v16',
      ].includes(scope)
        ? { capacity_mode: 'prior20_broker_reported', correlation_mode: 'prior60_positive_080' }
        : {}),
      ...(scope === 'automatic-hypotheses-v16' ? { allocation_mode: 'original' } : {}),
    })
    expect(fixed.strategies[0].config).toEqual(original.portfolio.strategies[0].config)
    expect(fixed.strategies[0].search).toEqual({})
    expect(original).toEqual(before)
    // Explicit chosen policies continue to take precedence over implicit defaults.
    original.portfolio.strategies[0].execution_policy = {
      ...fixed.strategies[0].execution_policy!,
      exit_mode: 'atr',
      atr_period: 14,
      atr_stop_multiple: 2,
      atr_target_multiple: 3,
    }
    expect(
      portfolioPayload({ ...original, optimizing: false }).strategies[0].execution_policy
    ).toEqual(original.portfolio.strategies[0].execution_policy)
  })

  it.each([
    'running',
    'failed',
  ])('offers study activity on demand for a %s optimization', async (status) => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      kind: 'portfolio_optimize',
      status,
    })
    vi.mocked(researchStudyActivity.get).mockImplementation(() => new Promise(() => {}))
    mount('/scanner-research?job=running-job')
    const button = await screen.findByRole('button', {
      name: 'Activity',
      exact: true,
    })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(researchStudyActivity.get).not.toHaveBeenCalled()
    await userEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(1)
    const signal = vi.mocked(researchStudyActivity.get).mock.calls[0][2]!
    await userEvent.click(button)
    expect(signal.aborted).toBe(true)
    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('does not offer optimizer activity for a backtest run', async () => {
    mount('/scanner-research?job=running-job')
    await screen.findByRole('button', { name: 'Cancel run', exact: true })
    expect(screen.queryByRole('button', { name: 'Activity', exact: true })).not.toBeInTheDocument()
    expect(researchStudyActivity.get).not.toHaveBeenCalled()
  })

  it.each([
    'paused',
    'interrupted',
    'cancelled',
    'failed',
  ])('loads saved trials only when opened on a %s study and aborts on close', async (status) => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      kind: 'portfolio_optimize',
      status,
      resumable: true,
    })
    vi.mocked(researchPartialStudy.get).mockImplementation(() => new Promise(() => {}))
    mount('/scanner-research?job=running-job')
    const button = await screen.findByRole('button', {
      name: 'Saved trials',
      exact: true,
    })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(researchPartialStudy.get).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Resume run' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Activity', exact: true })).toBeVisible()
    await userEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(researchPartialStudy.get).toHaveBeenCalledTimes(1)
    const signal = vi.mocked(researchPartialStudy.get).mock.calls[0][2]!
    await userEvent.click(button)
    expect(signal.aborted).toBe(true)
    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('does not offer partial trials for a running study or backtest', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      kind: 'portfolio_optimize',
    })
    mount('/scanner-research?job=running-job')
    await screen.findByRole('button', { name: 'Cancel run', exact: true })
    expect(screen.queryByRole('button', { name: 'Saved trials' })).not.toBeInTheDocument()
    expect(researchPartialStudy.get).not.toHaveBeenCalled()
  })

  it('limits suggestions to compatible enabled rules and keeps capital, costs and chosen axes unchanged', () => {
    const daily = draft()
    daily.optimization.trials = 70
    daily.portfolio.strategies[0].search = {
      target_pct: { min: 10, max: 30, step: 10 },
    }
    const automatic = applySuggestedSearch(daily, true)
    expect(automatic.portfolio.strategies[0].search).toEqual(daily.portfolio.strategies[0].search)
    const suggested = applySuggestedSearch(daily)
    expect(suggested.optimization.trials).toBe(70)
    expect(suggested.portfolio.strategies[0].search.target_pct).toEqual({
      min: 10,
      max: 30,
      step: 10,
    })
    expect(suggested.portfolio.strategies[0].search.hold_sessions).toEqual({
      min: 3,
      max: 7,
      step: 2,
    })
    expect(suggested.portfolio.strategies[0].config).toEqual(daily.portfolio.strategies[0].config)
    expect(suggested.portfolio.capital).toBe(daily.portfolio.capital)
    expect(suggested.portfolio.strategies[0].search.order_size_pct).toBeUndefined()
    expect(suggested.portfolio.strategies[0].search.allocation_pct).toBeUndefined()
    const timed = structuredClone(daily.portfolio.strategies[0])
    timed.config = {
      ...timed.config,
      trade_horizon: 'intraday',
      hold_minutes: 60,
      target_pct: 0,
      stop_pct: 0,
      trailing_enabled: true,
      trailing_pct: 2,
    }
    const minute = suggestedStrategySearch(timed, 'vectorbt')
    expect(minute).toEqual({
      hold_minutes: { min: 30, max: 90, step: 30 },
      trailing_pct: { min: 1, max: 3, step: 1 },
    })
    expect(suggestedStrategySearch(timed, 'nautilus').trailing_pct).toBeUndefined()
    timed.config.hold_minutes = null
    timed.config.trailing_enabled = false
    expect(suggestedStrategySearch(timed, 'vectorbt')).toEqual({})
    const empty = {
      ...daily,
      optimizing: true,
      portfolio: { ...daily.portfolio, strategies: [{ ...timed, search: {} }] },
    }
    expect(portfolioDraftIssue(empty)).toContain('Choose at least one setting')
  })

  it('starts new CSV optimizations with 25 trials and a visible complete suggested search', async () => {
    savedDraft()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Optimize', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Custom search', exact: true }))
    expect(screen.getByLabelText('Trials')).toHaveValue(25)
    expect(screen.getByRole('button', { name: 'Suggested search' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'Optimization ranges' })).toHaveTextContent(
      'Holding sessions 3–7 (step 2)'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Run optimization' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(portfolioResearch.preflight).mock.calls[0][0]
    expect(sent.optimization).toMatchObject({
      trials: 25,
      sampler: 'tpe',
      objective: 'balanced',
    })
    expect(sent.strategies[0].search).toEqual({
      hold_sessions: { min: 3, max: 7, step: 2 },
      target_pct: { min: 5, max: 15, step: 5 },
      stop_pct: { min: 2.5, max: 7.5, step: 2.5 },
    })
  })

  it('names old inputs by count and saved time without guessing they are duplicates', async () => {
    const old = {
      ...source('c'.repeat(32)),
      created_at: 1788958800,
      receipt: {
        ...source('c').receipt,
        signal_count: 1,
        symbol_count: 1,
        date_from: '2026-01-05',
        date_to: '2026-01-05',
      },
    }
    vi.mocked(portfolioResearch.sources).mockResolvedValue({
      items: [old, { ...old, id: 'd'.repeat(32), created_at: old.created_at + 60 }],
      next_offset: null,
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    const dialog = await screen.findByRole('dialog', { name: 'Saved signals' })
    const rows = await within(dialog).findAllByRole('button', {
      name: /1 signal/,
    })
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).not.toEqual(rows[1].textContent)
    for (const row of rows) {
      expect(row).toHaveTextContent('1 symbol')
      expect(row).toHaveTextContent('Saved ')
      expect(row).not.toHaveTextContent('1 signals')
      expect(row).not.toHaveTextContent('1 symbols')
      expect(row).not.toHaveTextContent('2026-01-05 – 2026-01-05')
    }
    await userEvent.click(rows[0])
    expect(screen.getByLabelText('Strategy 1 name')).toHaveValue('1 signal')
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.portfolio.strategies[0].source_id).toBe(old.id)
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
  })

  it('uses a retained filename when adding a saved strategy', async () => {
    const named = {
      ...source('c'.repeat(32)),
      receipt: { ...source('c').receipt, filename: 'My breakout.csv' },
    }
    vi.mocked(portfolioResearch.sources).mockResolvedValue({
      items: [named],
      next_offset: null,
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    await userEvent.click(await screen.findByRole('button', { name: /My breakout\.csv/ }))
    expect(screen.getByLabelText('Strategy 1 name')).toHaveValue('My breakout')
    expect(screen.getByLabelText('Strategy 1 name')).toHaveValue('My breakout')
    expect(screen.getByText(/4 signals · 2 symbols ·/)).toBeVisible()
    expect(portfolioResearch.upload).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
  })

  it('summarizes an imported Chartink source with signal, symbol and date coverage', async () => {
    const imported = draft()
    const receipt = imported.sources[imported.portfolio.strategies[0].source_id].receipt
    Object.assign(receipt, {
      filename: 'NKS BEST BUY STOCKS FOR INTRADAY.csv',
      signal_count: 365,
      symbol_count: 111,
      date_from: '2026-01-22',
      date_to: '2026-09-08',
      chartink: {
        url: 'https://chartink.com/screener/nks-best-buy',
        title: 'NKS BEST BUY STOCKS FOR INTRADAY',
        selected_period: '9 months',
        captured_at: '2026-09-11T06:00:00Z',
        repaints: true,
        export_kind: 'chartink_history_csv',
      },
    })
    savedDraft(imported)
    mount()
    expect(screen.getByText('365 signals · 111 symbols · 22 Jan–8 Sept 2026')).toBeVisible()
    expect(screen.queryByText(/NKS BEST BUY STOCKS FOR INTRADAY\.csv/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Chartink history' })).toHaveAttribute(
      'href',
      'https://chartink.com/screener/nks-best-buy'
    )
    expect(screen.getByText('· 9 months')).toBeVisible()
    expect(screen.getByText('· Repainting scanner')).toBeVisible()
    expect(portfolioResearch.upload).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
  })

  it('adds saved signals by their original source ID without uploading or preparing prices', async () => {
    const raw = {
      ...source('c'.repeat(32)),
      receipt: { ...source('c').receipt, name: 'Quick signals' },
    }
    const combined = {
      ...source('d'.repeat(32)),
      receipt: {
        ...source('d').receipt,
        name: 'Combined portfolio',
        input_type: 'portfolio',
      },
    }
    vi.mocked(portfolioResearch.sources).mockResolvedValue({
      items: [raw, combined],
      next_offset: null,
    })
    mount()
    expect(portfolioResearch.sources).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    const dialog = await screen.findByRole('dialog', { name: 'Saved signals' })
    const item = await within(dialog).findByRole('button', {
      name: /Quick signals/,
    })
    expect(item).toHaveTextContent('2026-01-05 – 2026-01-08')
    expect(item).toHaveTextContent('4 signals')
    expect(item).toHaveTextContent('2 symbols')
    expect(within(dialog).queryByText('Combined portfolio')).not.toBeInTheDocument()
    await userEvent.dblClick(item)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Strategy 1 name')).toHaveValue('Quick signals')
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.portfolio.strategies).toHaveLength(1)
    expect(stored.portfolio.strategies[0].source_id).toBe(raw.id)
    expect(stored.sources[raw.id].receipt.signal_count).toBe(4)
    expect(portfolioResearch.upload).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Use saved signals' })).toHaveFocus()
    )
  })

  it('pages saved inputs and keeps cached source choices scoped to the account', async () => {
    const first = {
      ...source('c'.repeat(32)),
      receipt: { ...source('c').receipt, name: 'First page' },
    }
    const next = {
      ...source('d'.repeat(32)),
      receipt: { ...source('d').receipt, name: 'Next page' },
    }
    vi.mocked(portfolioResearch.sources).mockImplementation(async (offset) =>
      offset === 20 ? { items: [next], next_offset: null } : { items: [first], next_offset: 20 }
    )
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    expect(await screen.findByRole('button', { name: /First page/ })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'More signals' }))
    expect(await screen.findByRole('button', { name: /Next page/ })).toBeVisible()
    expect(portfolioResearch.sources).toHaveBeenLastCalledWith(20, expect.any(AbortSignal))
    vi.mocked(portfolioResearch.sources).mockResolvedValue({
      items: [],
      next_offset: null,
    })
    await act(async () =>
      useAuthStore.setState({
        user: {
          username: 'other',
          broker: null,
          isLoggedIn: true,
          loginTime: null,
        },
      })
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    expect(
      await screen.findByText('No saved signals on this page. Upload a CSV to add a strategy.')
    ).toBeVisible()
    expect(screen.queryByRole('button', { name: /First page|Next page/ })).not.toBeInTheDocument()
    expect(portfolioResearch.sources).toHaveBeenLastCalledWith(0, expect.any(AbortSignal))
  })

  it('caps saved-source additions at eight even after repeated selection events', async () => {
    let initial = freshPortfolioDraft()
    for (let index = 0; index < 7; index++)
      initial = addPortfolioSource(initial, source('a'.repeat(32)), `Strategy ${index + 1}.csv`)
    savedDraft(initial)
    vi.mocked(portfolioResearch.sources).mockResolvedValue({
      items: [
        {
          ...source('b'.repeat(32)),
          receipt: { ...source('b').receipt, name: 'Eighth signals' },
        },
      ],
      next_offset: null,
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Use saved signals' }))
    await userEvent.dblClick(await screen.findByRole('button', { name: /Eighth signals/ }))
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.portfolio.strategies).toHaveLength(8)
    expect(new Set(stored.portfolio.strategies.map((row: { id: string }) => row.id)).size).toBe(8)
    expect(screen.queryByRole('button', { name: 'Use saved signals' })).not.toBeInTheDocument()
    expect(addPortfolioSource(stored, source('c'.repeat(32)), 'Too many.csv')).toBe(stored)
  })

  it('keeps the saved-signals picker accessible and returns focus on dismissal', async () => {
    mount()
    const opener = screen.getByRole('button', { name: 'Use saved signals' })
    await userEvent.click(opener)
    await screen.findByText('No saved signals on this page. Upload a CSV to add a strategy.')
    await act(async () => expect((await axe(document.body)).violations).toEqual([]))
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(opener).toHaveFocus())
  })

  it('starts with one upload action, shared cash, and no legacy or engine controls', () => {
    mount()
    expect(screen.getByRole('heading', { name: 'Backtest & Optimize' })).toBeVisible()
    expect(screen.getByLabelText('Shared starting cash (₹)')).toHaveValue(100000)
    expect(screen.getByRole('button', { name: 'Add signal CSV' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Import from Chartink' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeDisabled()
    for (const label of [
      'Backtesting engine',
      'Scanner-count trigger',
      'Candle interval',
      'Profit target (%)',
    ])
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    expect(screen.queryByText('Prepare prices')).not.toBeInTheDocument()
    expect(screen.getByText('CSV format & examples').closest('details')).not.toHaveAttribute('open')
  })

  it('offers Chartink connection from an empty setup without preparing or changing signals', async () => {
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Import from Chartink' }))
    expect(screen.getByRole('dialog', { name: 'Chartink to OpenAlgo' })).toBeVisible()
    expect(portfolioResearch.upload).not.toHaveBeenCalled()
    expect(portfolioResearch.sources).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Import from Chartink' })).toHaveFocus()
  })

  it.each([
    ['daily', 'D', 'Date,Symbol'],
    ['timed', '1m', 'Date,Time,Symbol'],
  ])('lets a new user inspect and upload the %s CSV without preparing prices', async (kind, interval, header) => {
    vi.mocked(portfolioResearch.upload).mockResolvedValue(source('a'.repeat(32), interval))
    mount()
    await userEvent.click(screen.getByText('CSV format & examples'))
    expect(screen.getByText(/Illustrative signals only/)).toBeVisible()
    const download = screen.getByRole('link', {
      name: `Download ${kind} example`,
    })
    expect(download).toHaveAttribute('download', `example-${kind}-signals.csv`)
    const csv = decodeURIComponent(download.getAttribute('href')!.split(',').slice(1).join(','))
    expect(csv.split('\r\n')[0]).toBe(header)
    expect(portfolioResearch.upload).not.toHaveBeenCalled()
    expect(portfolioResearch.sources).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
    if (kind === 'daily')
      await act(async () => expect((await axe(document.body)).violations).toEqual([]))

    const file = new File([csv], download.getAttribute('download')!, {
      type: 'text/csv',
    })
    await userEvent.upload(screen.getByLabelText('Upload strategy CSV files'), file)
    expect(await screen.findByLabelText('Strategy 1 name')).toHaveValue(`example-${kind}-signals`)
    expect(portfolioResearch.upload).toHaveBeenCalledWith(file)
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Candle interval')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const submitted = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
    expect(submitted.strategies[0].source_id).toBe('a'.repeat(32))
    expect(submitted.strategies[0].config.hold_minutes).toBeUndefined()
    expect(submitted.strategies[0].config.trade_horizon).toBeUndefined()
    expect(submitted).not.toHaveProperty('interval')
  })

  it('uploads two CSV strategies and splits untouched allocations equally', async () => {
    vi.mocked(portfolioResearch.upload)
      .mockResolvedValueOnce(source('a'.repeat(32)))
      .mockResolvedValueOnce(source('b'.repeat(32)))
    mount()
    await userEvent.upload(screen.getByLabelText('Upload strategy CSV files'), [
      new File(['date,symbol'], 'Breakout.csv', { type: 'text/csv' }),
      new File(['date,symbol'], 'Momentum.csv', { type: 'text/csv' }),
    ])
    expect(await screen.findByLabelText('Allocation for Breakout (%)')).toHaveValue(50)
    expect(screen.getByLabelText('Allocation for Momentum (%)')).toHaveValue(50)
    expect(screen.getByText('100% allocated')).toBeVisible()
    expect(portfolioResearch.upload).toHaveBeenCalledTimes(2)
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
  })

  it('shows confirmed file progress while the next CSV is still being read', async () => {
    let completeFirst!: (value: ResearchSource) => void
    let completeSecond!: (value: ResearchSource) => void
    vi.mocked(portfolioResearch.upload)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            completeFirst = resolve
          })
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            completeSecond = resolve
          })
      )
    mount()
    await userEvent.upload(screen.getByLabelText('Upload strategy CSV files'), [
      new File(['Symbol,Date\nINFY,2026-01-05'], 'First.csv', {
        type: 'text/csv',
      }),
      new File(['Symbol,Date\nTCS,2026-01-05'], 'Second.csv', {
        type: 'text/csv',
      }),
    ])
    expect(screen.getByText('Reading CSV 1 of 2')).toBeVisible()
    expect(screen.getByRole('progressbar', { name: 'Reading CSV file' })).not.toHaveAttribute(
      'value'
    )
    expect(screen.queryByText(/signals accepted/)).not.toBeInTheDocument()
    await act(async () => completeFirst(source('a'.repeat(32))))
    expect(screen.getByText('Reading CSV 2 of 2')).toBeVisible()
    expect(screen.getByText('1 file processed · 4 signals accepted')).toBeVisible()
    expect(screen.getByText('Second.csv')).toBeVisible()
    await act(async () => completeSecond(source('b'.repeat(32))))
    expect(screen.queryByRole('progressbar', { name: 'Reading CSV file' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Strategy 1 name')).toHaveValue('First')
    expect(screen.getByLabelText('Strategy 2 name')).toHaveValue('Second')
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
  })

  it('preserves chosen weights when another strategy is added and offers explicit equal split', async () => {
    savedDraft()
    mount()
    fireEvent.change(screen.getByLabelText('Allocation for Breakout (%)'), {
      target: { value: '30' },
    })
    vi.mocked(portfolioResearch.upload).mockResolvedValueOnce(source('b'.repeat(32)))
    await userEvent.upload(
      screen.getByLabelText('Upload strategy CSV files'),
      new File(['date,symbol'], 'Momentum.csv', { type: 'text/csv' })
    )
    expect(await screen.findByLabelText('Allocation for Momentum (%)')).toHaveValue(70)
    expect(screen.getByLabelText('Allocation for Breakout (%)')).toHaveValue(30)
    await userEvent.click(screen.getByRole('button', { name: 'Equal split' }))
    expect(screen.getByLabelText('Allocation for Breakout (%)')).toHaveValue(50)
  })

  it('shows suggested ranges, lets the user opt out of one, and preserves choices across mode switches', async () => {
    savedDraft()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Optimize', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Custom search', exact: true }))
    expect(screen.getByRole('region', { name: 'Optimization ranges' })).toHaveTextContent(
      'Profit target 5–15% (step 5)'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Edit search for Breakout' }))
    expect(screen.getByLabelText('Profit target (%) min')).toHaveValue(5)
    expect(screen.getByLabelText('Stop loss (%) min')).toHaveValue(2.5)
    await userEvent.click(screen.getByLabelText('Optimize Stop loss (%)'))
    expect(screen.queryByLabelText('Stop loss (%) min')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Profit target (%) max'), {
      target: { value: '20' },
    })
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    await userEvent.click(screen.getByRole('button', { name: 'Backtest', exact: true }))
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(portfolioPayload(stored).strategies[0].search).toEqual({})
    expect(stored.portfolio.strategies[0].search.target_pct.max).toBe(20)
    await userEvent.click(screen.getByRole('button', { name: 'Optimize', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Settings for Breakout' }))
    expect(screen.getByLabelText('Profit target (%) max')).toHaveValue(20)
    expect(screen.queryByLabelText('Stop loss (%) min')).not.toBeInTheDocument()
  })

  it('uses intraday requirements only when selected and keeps candle choice automatic', async () => {
    savedDraft()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Settings for Breakout' }))
    await userEvent.selectOptions(screen.getByLabelText('Holding period'), 'intraday')
    expect(screen.getByLabelText('Maximum holding minutes')).toHaveValue(60)
    expect(screen.queryByLabelText('Maximum holding sessions')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Candle interval')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalled())
    expect(vi.mocked(portfolioResearch.submit).mock.calls[0][0].strategies[0].config).toMatchObject(
      { trade_horizon: 'intraday', hold_minutes: 60 }
    )
  })

  it('validates and submits one full portfolio job from the only Run button', async () => {
    savedDraft()
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    expect(portfolioResearch.preflight).toHaveBeenCalledTimes(1)
    const [payload, requestId] = vi.mocked(portfolioResearch.submit).mock.calls[0]
    expect(payload.engine).toBe('vectorbt')
    expect(payload.optimization).toBeUndefined()
    expect(payload.strategies[0].source_id).toBe('a'.repeat(32))
    expect(requestId).toBeTruthy()
    expect(
      await screen.findByRole('progressbar', { name: 'Running your backtest' })
    ).not.toHaveAttribute('value')
  })

  it('rejects invalid allocations and runs the visible suggested optimization after correcting them', async () => {
    const invalid = addPortfolioSource(draft(), source('b'.repeat(32)), 'Momentum.csv')
    invalid.portfolio.strategies.forEach((item) => {
      item.allocation_pct = 70
    })
    savedDraft(invalid)
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('at most 100%')
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Equal split' }))
    await userEvent.click(screen.getByRole('button', { name: 'Optimize', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Custom search', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Run optimization' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(portfolioResearch.preflight).mock.calls[0][0]
    expect(payload.optimization?.trials).toBe(25)
    expect(payload.strategies.every((item) => Object.keys(item.search).length > 0)).toBe(true)
  })

  it('keeps the same request identity after an uncertain submission error', async () => {
    savedDraft()
    vi.mocked(portfolioResearch.submit)
      .mockRejectedValueOnce(new Error('Connection interrupted'))
      .mockResolvedValueOnce(running)
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Connection interrupted')
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(2))
    expect(vi.mocked(portfolioResearch.submit).mock.calls[0][1]).toBe(
      vi.mocked(portfolioResearch.submit).mock.calls[1][1]
    )
  })

  it('offers cancellation and checkpoint resume without a new-price control', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue(running)
    vi.mocked(portfolioResearch.cancel).mockResolvedValue({
      ...running,
      status: 'cancel_requested',
    })
    const first = mount('/scanner-research?job=running-job')
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel run' }))
    expect(portfolioResearch.cancel).toHaveBeenCalledWith('running-job', expect.any(AbortSignal))
    first.unmount()
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      status: 'interrupted',
      resumable: true,
    })
    vi.mocked(portfolioResearch.resume).mockResolvedValue(running)
    mount('/scanner-research?job=running-job')
    await userEvent.click(await screen.findByRole('button', { name: 'Resume run' }))
    expect(portfolioResearch.resume).toHaveBeenCalledWith('running-job', expect.any(AbortSignal))
  })

  it('pauses a ready study, keeps checking while saving, and resumes the same saved run', async () => {
    const ready = { ...running, kind: 'portfolio_optimize', pausable: true }
    const pausing = { ...ready, status: 'pausing', pausable: false }
    const paused = { ...pausing, status: 'paused', resumable: true }
    vi.mocked(portfolioResearch.job).mockResolvedValue(ready)
    vi.mocked(portfolioResearch.pause).mockImplementation(async () => {
      vi.mocked(portfolioResearch.job).mockResolvedValue(pausing)
      return pausing
    })
    const route = '?job=running-job&return_research=experiment%3Doriginal%26view%3Dstudies'
    mount(`/scanner-research${route}`)
    await userEvent.click(await screen.findByRole('button', { name: 'Pause', exact: true }))
    expect(portfolioResearch.pause).toHaveBeenCalledWith('running-job', expect.any(AbortSignal))
    expect(await screen.findByRole('button', { name: 'Pausing…' })).toBeDisabled()
    expect(screen.getByTestId('run-location')).toHaveTextContent(route)
    expect(screen.getByRole('button', { name: 'Cancel run' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Resume run' })).not.toBeInTheDocument()
    vi.mocked(portfolioResearch.job).mockResolvedValue(paused)
    await act(async () => {
      await clients.at(-1)!.invalidateQueries({ queryKey: ['portfolio-job'] })
    })
    expect(await screen.findByText('Progress saved. Resume whenever you’re ready.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Pause', exact: true })).not.toBeInTheDocument()
    vi.mocked(portfolioResearch.resume).mockImplementation(async () => {
      vi.mocked(portfolioResearch.job).mockResolvedValue(ready)
      return ready
    })
    await userEvent.click(screen.getByRole('button', { name: 'Resume run' }))
    expect(portfolioResearch.resume).toHaveBeenCalledWith('running-job', expect.any(AbortSignal))
    expect(await screen.findByRole('button', { name: 'Pause', exact: true })).toBeEnabled()
    expect(screen.getByTestId('run-location')).toHaveTextContent(route)
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
  })

  it('offers pause only when the server confirms a durable study checkpoint', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      kind: 'portfolio_optimize',
      pausable: false,
    })
    mount('/scanner-research?job=running-job')
    await screen.findByRole('button', { name: 'Cancel run' })
    expect(screen.queryByRole('button', { name: 'Pause', exact: true })).not.toBeInTheDocument()
    expect(portfolioResearch.pause).not.toHaveBeenCalled()
  })

  it('permits a safe pause retry after a lost response and does not submit a second study', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      kind: 'portfolio_optimize',
      pausable: true,
    })
    vi.mocked(portfolioResearch.pause).mockRejectedValueOnce(new Error('Connection interrupted'))
    mount('/scanner-research?job=running-job')
    await userEvent.click(await screen.findByRole('button', { name: 'Pause', exact: true }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Connection interrupted')
    const paused = {
      ...running,
      kind: 'portfolio_optimize',
      status: 'paused',
      resumable: true,
    }
    vi.mocked(portfolioResearch.pause).mockResolvedValueOnce(paused)
    vi.mocked(portfolioResearch.job).mockResolvedValue(paused)
    await userEvent.click(screen.getByRole('button', { name: 'Pause', exact: true }))
    await screen.findByRole('button', { name: 'Resume run' })
    expect(vi.mocked(portfolioResearch.pause).mock.calls.map(([id]) => id)).toEqual([
      'running-job',
      'running-job',
    ])
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
  })

  it('aborts a pending pause request on unmount and ignores its late response', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      kind: 'portfolio_optimize',
      pausable: true,
    })
    let finish!: (job: PortfolioJob) => void
    vi.mocked(portfolioResearch.pause).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    const view = mount('/scanner-research?job=running-job')
    await userEvent.click(await screen.findByRole('button', { name: 'Pause', exact: true }))
    expect(screen.getByRole('button', { name: 'Pause', exact: true })).toBeDisabled()
    const signal = vi.mocked(portfolioResearch.pause).mock.calls[0][1]!
    view.unmount()
    expect(signal.aborted).toBe(true)
    await act(async () => {
      finish({ ...running, status: 'paused', resumable: true })
    })
    expect(
      clients.at(-1)!.getQueryData<PortfolioJob>(['portfolio-job', 'account', 'running-job'])
        ?.status
    ).toBe('running')
  })

  it('ignores a pause response after the account changes', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      kind: 'portfolio_optimize',
      pausable: true,
    })
    let finish!: (job: PortfolioJob) => void
    vi.mocked(portfolioResearch.pause).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    mount('/scanner-research?job=running-job')
    await userEvent.click(await screen.findByRole('button', { name: 'Pause', exact: true }))
    const signal = vi.mocked(portfolioResearch.pause).mock.calls[0][1]!
    act(() => {
      useAuthStore.setState({
        user: {
          username: 'different-account',
          broker: null,
          isLoggedIn: true,
          loginTime: null,
        },
      })
    })
    expect(signal.aborted).toBe(true)
    await act(async () => {
      finish({ ...running, status: 'paused', resumable: true })
    })
    expect(screen.queryByText('Study paused')).not.toBeInTheDocument()
    expect(
      clients.at(-1)!.getQueryData<PortfolioJob>(['portfolio-job', 'account', 'running-job'])
        ?.status
    ).toBe('running')
  })

  it('keeps an archived study read-only while showing its saved progress', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({
      ...running,
      kind: 'portfolio_optimize',
      pausable: true,
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    clients.push(client)
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/scanner-research?job=running-job']}>
          <PortfolioResearch
            workspace={{
              draft: draft(),
              onChange: vi.fn(),
              onRun: vi.fn(),
              onOpenJob: vi.fn(),
              readOnly: true,
            }}
          />
        </MemoryRouter>
      </QueryClientProvider>
    )
    await screen.findByRole('region', { name: 'Portfolio run progress' })
    expect(
      screen.queryByRole('button', { name: /Pause|Resume run|Cancel run/ })
    ).not.toBeInTheDocument()
    expect(portfolioResearch.pause).not.toHaveBeenCalled()
  })

  it('opens saved portfolios and routes earlier runs through the legacy callback', async () => {
    const earlier = {
      ...finished,
      id: 'old-job',
      kind: 'backtest',
      title: 'Earlier fixed backtest',
      specification: {},
      result: undefined,
    }
    vi.mocked(portfolioResearch.jobs).mockResolvedValue({
      items: [finished, earlier],
      next_cursor: null,
    })
    const legacy = vi.fn()
    mount('/scanner-research', legacy)
    await userEvent.click(screen.getByRole('button', { name: 'Saved runs' }))
    const modal = await screen.findByRole('dialog')
    expect(await within(modal).findByRole('button', { name: /My portfolio/ })).toBeVisible()
    expect(
      within(modal).queryByRole('button', { name: /Earlier fixed backtest/ })
    ).not.toBeInTheDocument()
    await userEvent.click(within(modal).getByRole('button', { name: 'Earlier runs' }))
    await userEvent.click(within(modal).getByRole('button', { name: /Earlier fixed backtest/ }))
    expect(legacy).toHaveBeenCalledWith('old-job')
  })

  it('removes unused source receipts and keeps drafts scoped to the account', async () => {
    savedDraft()
    sessionStorage.setItem('portfolio-draft:other', JSON.stringify(draft()))
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Remove Breakout' }))
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.sources).toEqual({})
    expect(
      JSON.parse(sessionStorage.getItem('portfolio-draft:other')!).portfolio.strategies
    ).toHaveLength(1)
  })

  it('reserves later data before both baseline and optimization without erasing the choice', async () => {
    const initial = draft()
    initial.optimizing = true
    initial.portfolio.strategies[0].search.target_pct = {
      min: 5,
      max: 15,
      step: 5,
    }
    savedDraft(initial)
    mount()
    await userEvent.click(screen.getByLabelText('Reserve a later period'))
    let stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(portfolioPayload(stored).validation).toEqual({
      train_pct: 80,
      mode: 'reserve',
    })
    await userEvent.click(screen.getByRole('button', { name: 'Backtest', exact: true }))
    stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(portfolioPayload(stored).validation).toEqual({
      train_pct: 80,
      mode: 'reserve',
    })
    expect(stored.portfolio.validation).toEqual({
      train_pct: 80,
      mode: 'reserve',
    })
  })

  it('preserves legacy validation request semantics and unreserved full-period drafts', () => {
    const initial = draft()
    expect(portfolioPayload(initial).validation).toBeUndefined()
    initial.portfolio.validation = { train_pct: 80 }
    initial.optimizing = false
    expect(portfolioPayload(initial).validation).toBeUndefined()
    initial.optimizing = true
    expect(portfolioPayload(initial).validation).toEqual({ train_pct: 80 })
  })
})

describe('portfolio engine capabilities', () => {
  it('routes an automatic finding to an exact backtest before offering saved-setup decisions', async () => {
    const automaticResult: PortfolioResult = {
      ...result,
      report_context: {
        version: 'research-report-context-v1',
        report_id: 'report',
        job_id: 'auto',
        result_artifact: 'artifact',
        inputs_artifact: 'inputs',
        config_id: 'baseline',
        period: 'selection',
        period_label: 'Search period',
        dates: { from: '2026-01-01', to: '2026-05-31' },
        analysis_version: null,
        analysis_artifact: null,
      },
      automatic_research: {
        version: 'automatic-trade-management-v1',
        status: 'not_supported',
        headline: 'No reliable improvement over unchanged settings',
        selected_config_id: 'baseline',
        selected_is_baseline: true,
        selection_basis: 'Baseline retained.',
        recipe: {
          version: 'automatic-trade-management-v1',
          periods: {
            search: { from: '2026-01-01', to: '2026-05-31' },
            check1: { from: '2026-06-01', to: '2026-06-30' },
            check2: { from: '2026-07-01', to: '2026-07-31' },
            final: { from: '2026-08-01', to: '2026-09-10' },
          },
        },
        baseline: {},
        checks: [],
        final: null,
        counts: { proposals: 50, simulations: 60, finalists: 3 },
        unsupported_families: [],
      },
    }
    const replay = vi.fn()
    render(
      <PortfolioResults
        job={{
          ...finished,
          kind: 'portfolio_optimize',
          result: automaticResult,
        }}
        result={automaticResult}
        experimentId="saved-research"
        onRerun={replay}
        onOptimize={vi.fn()}
        onAdjust={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    expect(
      screen.queryByRole('button', {
        name: /Save|Optimize this|Adjust & test|Keep|Compare|Choose/,
      })
    ).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Backtest these settings' }))
    expect(replay).toHaveBeenCalledWith()
  })

  function allowNautilus() {
    vi.mocked(portfolioResearch.capabilities).mockResolvedValue({
      ...capabilities,
      engines: capabilities.engines.map((engine) => ({
        ...engine,
        available: true,
      })),
    })
  }

  it('offers Nautilus inside More settings only when its runtime is available', async () => {
    allowNautilus()
    savedDraft()
    mount()
    const picker = await screen.findByLabelText('Backtest engine')
    expect(picker).not.toBeVisible()
    await userEvent.click(screen.getByText('More settings'))
    expect(picker).toBeVisible()
    await userEvent.selectOptions(picker, 'nautilus')
    expect(screen.getByText('A different fill model; results can differ.')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalled())
    expect(vi.mocked(portfolioResearch.submit).mock.calls[0][0].engine).toBe('nautilus')
  })

  it('preserves incompatible settings and blocks execution instead of changing them', async () => {
    allowNautilus()
    const initial = draft()
    initial.portfolio.strategies[0].config.slippage_bps = 5
    initial.portfolio.strategies[0].config.trailing_enabled = true
    initial.portfolio.strategies[0].config.trailing_pct = 3
    savedDraft(initial)
    mount()
    await screen.findByLabelText('Backtest engine')
    await userEvent.click(screen.getByText('More settings'))
    await userEvent.selectOptions(screen.getByLabelText('Backtest engine'), 'nautilus')
    expect(screen.getByRole('alert')).toHaveTextContent(
      'set slippage to 0 and turn off trailing protection'
    )
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeDisabled()
    const stored = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(stored.portfolio.strategies[0].config).toMatchObject({
      slippage_bps: 5,
      trailing_enabled: true,
      trailing_pct: 3,
    })
    expect(stored.portfolio.strategies[0].source_id).toBe(initial.portfolio.strategies[0].source_id)
    await userEvent.selectOptions(screen.getByLabelText('Backtest engine'), 'vectorbt')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeEnabled()
  })

  it('keeps a saved unavailable engine selected and requires cent-precise cash for Nautilus', async () => {
    const initial = draft()
    initial.portfolio.engine = 'nautilus'
    savedDraft(initial)
    const view = mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('NautilusTrader is unavailable')
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeDisabled()
    expect(JSON.parse(sessionStorage.getItem('portfolio-draft:account')!).portfolio.engine).toBe(
      'nautilus'
    )
    view.unmount()
    allowNautilus()
    initial.portfolio.capital = 100000.123
    savedDraft(initial)
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('at most two decimal places')
  })

  it('records the actual engine in result Settings', async () => {
    render(
      <PortfolioResults
        job={finished}
        result={{
          ...result,
          execution: { engine: 'nautilus', engine_version: '1.221.0' },
        }}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    await userEvent.click(screen.getByRole('tab', { name: 'Settings' }))
    expect(screen.getByText('NautilusTrader 1.221.0', { selector: 'p' })).toBeVisible()
  })

  it('keeps builder and settings drawer accessible with labelled controls and focus return', async () => {
    savedDraft()
    const view = mount()
    await act(async () => {
      expect((await axe(view.container)).violations).toEqual([])
    })
    const button = screen.getByRole('button', {
      name: 'Settings for Breakout',
    })
    await userEvent.click(button)
    await act(async () => {
      expect((await axe(document.body)).violations).toEqual([])
    })
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(button).toHaveFocus())
  })
})

describe('portfolio result reading', () => {
  it('starts a condition backtest from the displayed saved analysis and opens its native job', async () => {
    const saved: PortfolioResult = {
      ...result,
      report_context: {
        version: 'research-report-context-v1',
        report_id: 'report',
        job_id: finished.id,
        result_artifact: 'report-artifact',
        inputs_artifact: 'inputs',
        config_id: 'config',
        period: 'full',
        period_label: 'Full period',
        dates: { from: '2026-01-05', to: '2026-01-08' },
        analysis_version: 'v1',
        analysis_artifact: 'conditions-artifact',
      },
      analysis: {
        version: 'v1',
        metrics: {},
        unavailable: {},
        catalog: [],
        charts: [],
        basis: [],
        market_conditions: {
          version: 'research-market-conditions-v1',
          status: 'available',
          descriptor: {
            symbol: 'NIFTY',
            exchange: 'NSE_INDEX',
            interval: 'D',
            role: 'benchmark',
          },
          dates: { from: '2026-01-05', to: '2026-01-08' },
          coverage: {
            total_sessions: 4,
            classified_sessions: 4,
            closed_trades: 20,
            classified_trades: 20,
            unclassified_trades: 0,
          },
          timeline: [],
          cohorts: [
            {
              strategy_id: strategy.id,
              strategy_name: strategy.name,
              dimension: 'trend',
              regime: 'up',
              label: 'Rising',
              closed_trades: 20,
              entry_sessions: 20,
              average_net_return_pct: 1,
              win_rate_pct: 50,
              net_pnl: 100,
              evidence: 'descriptive',
            },
          ],
          finding: {
            status: 'observed',
            text: 'Historical condition differences.',
            next_step: 'Test a condition.',
          },
          basis: [],
          recipe: {},
        },
      },
    }
    const open = vi.fn()
    vi.mocked(portfolioResearch.conditionReplay).mockResolvedValue({
      ...running,
      id: 'condition-job',
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    clients.push(client)
    const validation = vi
      .spyOn(researchValidation, 'context')
      .mockImplementation(() => new Promise(() => {}))
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <PortfolioResults
            job={finished}
            result={saved}
            experimentId="research"
            onOpenReport={open}
            onRerun={vi.fn()}
            rerunning={false}
            exportUrl="/export"
          />
        </MemoryRouter>
      </QueryClientProvider>
    )
    expect(portfolioResearch.conditionReplay).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Test rising condition' }))
    await waitFor(() => expect(open).toHaveBeenCalledWith('condition-job'))
    expect(portfolioResearch.conditionReplay).toHaveBeenCalledWith(
      finished.id,
      {
        experiment_id: 'research',
        strategy_id: strategy.id,
        dimension: 'trend',
        regime: 'up',
        analysis_artifact: 'conditions-artifact',
        period: 'selection',
        request_id: expect.any(String),
      },
      expect.any(AbortSignal)
    )
    validation.mockRestore()
  })

  it.each([
    'selection',
    'validation',
  ] as const)('keeps the %s condition comparison and only offers condition-safe selection actions', async (period) => {
    const saved: PortfolioResult = {
      ...result,
      condition_replay: {
        version: 'v1',
        id: 'condition',
        condition: {
          strategy_id: strategy.id,
          dimension: 'trend',
          regime: 'up',
          label: 'Rising',
        },
        parent_job_id: 'parent',
        parent_result_artifact: 'result',
        analysis_artifact: 'analysis',
        period,
        counts: {
          allowed: 2,
          filtered: 1,
          unknown: 1,
          original_excluded: 0,
          pending: 0,
          unaffected: 0,
        },
        baseline: {
          summary: {
            net_return_pct: 2,
            max_drawdown_pct: 1,
            closed_trades: 4,
          },
        },
        delta: {
          net_return_pct: -1,
          max_drawdown_pct: -0.5,
          closed_trades: -1,
        },
        basis: [],
      },
    }
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    clients.push(queryClient)
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <PortfolioResults
            job={finished}
            result={saved}
            experimentId="research"
            onOpenReport={vi.fn()}
            onRerun={vi.fn()}
            onOptimize={vi.fn()}
            onAdjust={vi.fn()}
            onEvaluate={vi.fn()}
            rerunning={false}
            exportUrl="/export"
          />
        </MemoryRouter>
      </QueryClientProvider>
    )
    expect(screen.getByRole('region', { name: 'Condition test' })).toBeVisible()
    if (period === 'selection') {
      expect(screen.getByRole('button', { name: 'Optimize this' })).toBeVisible()
      expect(screen.getByRole('button', { name: 'Adjust & test' })).toBeVisible()
      expect(screen.getByRole('button', { name: 'Shortlist', exact: true })).toBeVisible()
    } else {
      expect(
        screen.queryByRole('button', {
          name: /Optimize this|Adjust & test|Shortlist|Test later period/,
        })
      ).not.toBeInTheDocument()
    }
    expect(screen.queryByRole('button', { name: 'Test rising condition' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Export', exact: true })).toBeVisible()
    expect(portfolioResearch.conditionReplay).not.toHaveBeenCalled()
  })

  it('keeps a frozen comparison member on its primary saved period without latest-analysis or study actions', async () => {
    const frozen = {
      ...result,
      analysis: {
        version: 'research-analysis-v1',
        metrics: {},
        unavailable: {},
        catalog: [],
        charts: [],
        basis: [],
      },
      validation: {
        train_from: '2026-01-01',
        train_to: '2026-01-20',
        test_from: '2026-02-01',
        test_to: '2026-02-10',
        training_signals: 20,
        testing_signals: 5,
        label: 'Later period',
        selection_basis: 'Earlier only',
        result: { ...result, summary: { net_return_pct: 99 } },
      },
      reserved_evaluation: {
        version: 'research-period-plan-v1' as const,
        status: 'reserved' as const,
        selection: { from: '2026-01-01', to: '2026-01-20' },
        evaluation: { from: '2026-02-01', to: '2026-02-10' },
      },
      experiment: {
        kind: 'portfolio_optimize' as const,
        rows: [],
        recommendation_id: 'c',
        selected_strategies: [strategy],
        specification: {
          sampler: 'tpe' as const,
          trials: 10,
          objective: 'balanced' as const,
          seed: 0,
        },
        optimizer: {
          sampler: 'TPE',
          objective_definition: 'balanced',
          version: '5',
        },
        counts: {},
      },
    }
    render(
      <PortfolioResults
        job={{ ...finished, kind: 'portfolio_optimize' }}
        result={frozen}
        freezeAnalysis
        exportUrl="/latest-export"
        onRerun={vi.fn()}
        onOpenReport={vi.fn()}
        onEvaluate={vi.fn()}
        onOptimize={vi.fn()}
        rerunning={false}
      />
    )
    expect(screen.getByRole('tab', { name: 'Report' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: /Study|Trials/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Evaluation period')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', {
        name: /Replay|Optimize|Test later|Update report|Expand analysis|Prepare analysis/,
      })
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Export/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('All statistics', { selector: 'summary' }))
    expect(screen.queryByRole('link', { name: 'Export analysis' })).not.toBeInTheDocument()
    expect(portfolioResearch.analysis).not.toHaveBeenCalled()
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
    expect(researchStudyActivity.get).not.toHaveBeenCalled()
  })

  it('withholds unknown-currency cash and monetary charts in a frozen report', async () => {
    render(
      <ReportCurrency.Provider value={null}>
        <PortfolioResults
          job={finished}
          result={result}
          freezeAnalysis
          exportUrl="/export"
          onRerun={vi.fn()}
          rerunning={false}
        />
      </ReportCurrency.Provider>
    )
    expect(
      screen.getByText('Final equity', { selector: 'summary' }).closest('dt')?.parentElement
    ).toHaveTextContent('—')
    expect(await screen.findByLabelText('account-cumulative')).toBeVisible()
    expect(document.body.textContent).not.toMatch(/₹|\bINR\b/)
    expect(screen.getByRole('button', { name: 'Equity', exact: true })).toBeDisabled()
    await userEvent.click(screen.getByRole('tab', { name: 'Trades' }))
    expect(document.body.textContent).not.toMatch(/₹|\bINR\b/)
    await userEvent.click(screen.getByRole('tab', { name: 'Settings' }))
    expect(screen.getByText('Shared starting cash: —')).toBeVisible()
  })
  it('keeps reserved dates and exact later evaluation separate from the displayed baseline', async () => {
    const evaluate = vi.fn()
    const view = render(
      <PortfolioResults
        job={finished}
        result={{
          ...result,
          reserved_evaluation: {
            version: 'research-period-plan-v1',
            status: 'reserved',
            selection: { from: '2026-01-05', to: '2026-01-20' },
            evaluation: { from: '2026-02-01', to: '2026-02-05' },
          },
        }}
        onRerun={vi.fn()}
        onEvaluate={evaluate}
        rerunning={false}
        exportUrl="/export"
        embedded
      />
    )
    expect(screen.getByText(/Later period reserved/)).toHaveTextContent('2026-02-01')
    expect(screen.queryByRole('heading', { name: 'My portfolio' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Test later period' }))
    expect(evaluate).toHaveBeenCalledOnce()
    expect(
      screen.queryByRole('button', { name: 'Later period', exact: true })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Report' })).toHaveAttribute('aria-selected', 'true')
    view.unmount()
  })

  it('keeps the trial page when switching away from Trials and back', async () => {
    const selected: PortfolioResult = {
      ...result,
      experiment: {
        kind: 'portfolio_optimize',
        rows: Array.from({ length: 65 }, (_, index) => ({
          trial_number: index,
          config_id: `trial-${index}`,
          strategies: [strategy],
          summary: result.summary,
          score: 0.5,
          stage: 'tpe',
        })),
        recommendation_id: 'trial-0',
        selected_strategies: [strategy],
        specification: {
          sampler: 'tpe',
          trials: 100,
          objective: 'balanced',
          seed: 0,
        },
        optimizer: {
          sampler: 'TPESampler',
          objective_definition: 'return minus drawdown',
          version: '5',
        },
        counts: {
          evaluated_this_pass: 65,
          rejected_allocations: 0,
          reused_trials: 35,
        },
      },
    }
    render(
      <PortfolioResults
        job={finished}
        result={selected}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    await userEvent.click(screen.getByRole('tab', { name: 'Trials' }))
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('26–50 of 65')).toBeVisible()
    await userEvent.click(screen.getByRole('tab', { name: 'Report' }))
    await userEvent.click(screen.getByRole('tab', { name: 'Trials' }))
    expect(screen.getByText('26–50 of 65')).toBeVisible()
  })

  it('leads with marked account metrics and keeps timestamped chart points intact', async () => {
    render(
      <PortfolioResults
        job={finished}
        result={result}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    expect(
      screen.getByText('Final equity', { selector: 'summary' }).closest('dt')?.parentElement
    ).toHaveTextContent('₹1,01,000')
    expect(screen.getByText('Available cash', { selector: 'dt' }).parentElement).toHaveTextContent(
      '₹99,000'
    )
    expect(screen.getByRole('heading', { name: 'Strategy contributions' })).toBeVisible()
    expect(await screen.findByLabelText('account-cumulative')).toHaveTextContent(
      '2026-01-06T09:16:00+05:30'
    )
    expect(screen.queryByText('target; open exit')).not.toBeInTheDocument()
  })

  it('opens per-strategy trades and paginates the ledger instead of rendering everything', async () => {
    const many = {
      ...result,
      ledger: Array.from({ length: 55 }, (_, index) => ({
        ...result.ledger[0],
        source_row: index + 2,
        symbol: `TEST${index}`,
      })),
    }
    render(
      <PortfolioResults
        job={finished}
        result={many}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Breakout' }))
    expect(screen.getByLabelText('Filter trades by strategy')).toHaveValue(strategy.id)
    expect(screen.getByText('TEST24')).toBeVisible()
    expect(screen.queryByText('TEST25')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('TEST25')).toBeVisible()
    expect(screen.queryByText('TEST0')).not.toBeInTheDocument()
  })

  it('shows one partial-coverage line and exact candidate rerun controls', async () => {
    const selected: PortfolioResult = {
      ...result,
      source: { ...result.source!, excluded_signals: 1 },
      experiment: {
        kind: 'portfolio_optimize',
        rows: [
          {
            trial_number: 0,
            config_id: 'exact-trial',
            strategies: [strategy],
            summary: result.summary,
            score: 0.5,
            stage: 'tpe',
          },
        ],
        recommendation_id: 'exact-trial',
        selected_strategies: [strategy],
        specification: {
          sampler: 'tpe',
          trials: 50,
          objective: 'balanced',
          seed: 0,
        },
        optimizer: {
          sampler: 'TPESampler',
          objective_definition: 'return minus drawdown',
          version: '5.0.0',
        },
        counts: {
          evaluated_this_pass: 1,
          rejected_allocations: 2,
          reused_trials: 0,
        },
      },
    }
    const rerun = vi.fn()
    render(
      <PortfolioResults
        job={finished}
        result={selected}
        onRerun={rerun}
        rerunning={false}
        exportUrl="/export"
      />
    )
    expect(screen.getAllByText('1 signal excluded — view reasons')).toHaveLength(1)
    await userEvent.click(screen.getByRole('tab', { name: 'Trials' }))
    await userEvent.click(screen.getByRole('button', { name: 'Backtest this' }))
    expect(rerun).toHaveBeenCalledWith('exact-trial')
    await userEvent.click(screen.getByRole('button', { name: 'View settings' }))
    expect(screen.getByRole('region', { name: 'Trial strategy settings' })).toHaveTextContent(
      'Profit target'
    )
  })

  it('keeps later-period results separate from earlier search trials', async () => {
    const validationResult: PortfolioResult = {
      ...result,
      summary: { ...result.summary, final_equity: 99800, net_pnl: -200 },
      equity_curve: [
        {
          ...result.equity_curve[0],
          date: '2026-02-01',
          timestamp: undefined,
          equity: 99800,
        },
      ],
    }
    const combined: PortfolioResult = {
      ...result,
      validation: {
        train_from: '2026-01-05',
        train_to: '2026-01-20',
        test_from: '2026-02-01',
        test_to: '2026-02-05',
        training_signals: 20,
        testing_signals: 5,
        label: 'Later period',
        selection_basis: 'settings chosen on earlier period only',
        result: validationResult,
      },
    }
    render(
      <PortfolioResults
        job={finished}
        result={combined}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Later period', exact: true }))
    expect(
      screen.getByText('Final equity', { selector: 'summary' }).closest('dt')?.parentElement
    ).toHaveTextContent('₹99,800')
    expect(screen.queryByRole('button', { name: 'Run again' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Trials' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Earlier period', exact: true }))
    expect(
      screen.getByText('Final equity', { selector: 'summary' }).closest('dt')?.parentElement
    ).toHaveTextContent('₹1,01,000')
  })
})

it('uses all available history automatically without an extra option or changing trading rules', async () => {
  const original = selectPortfolioRun(draft(), true)
  savedDraft(original)
  mount()
  await userEvent.click(await screen.findByText('Advanced research options'))
  expect(
    screen.queryByRole('checkbox', { name: 'Historical optimisation' })
  ).not.toBeInTheDocument()
  expect(screen.getByText(/Uses available history, searches trade sizes/)).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
  await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
  const sent = vi.mocked(portfolioResearch.submit).mock.calls[0][0]
  expect(sent.automatic_research).toEqual({
    version: 'automatic-hypotheses-v16',
    history_policy: 'available_history',
    design_policy: 'automatic_design_v1',
    decision_policy: 'automatic_decision_v1',
    action_policy: 'automatic_actions_v1',
  })
  expect(sent.capital).toBe(original.portfolio.capital)
  expect(sent.strategies[0].config).toEqual(original.portfolio.strategies[0].config)
  expect(sent.strategies[0].allocation_pct).toBe(original.portfolio.strategies[0].allocation_pct)
  expect(original.portfolio.automatic_research).toEqual({
    version: 'automatic-hypotheses-v16',
    history_policy: 'available_history',
    design_policy: 'automatic_design_v1',
    decision_policy: 'automatic_decision_v1',
    action_policy: 'automatic_actions_v1',
  })
})

it('opts an existing strict v16 draft into available history on a new run without rewriting it', async () => {
  const original = draft()
  original.optimizing = true
  original.portfolio.automatic_research = { version: 'automatic-hypotheses-v16' }
  const before = structuredClone(original)
  savedDraft(original)
  mount()
  await userEvent.click(await screen.findByRole('button', { name: 'Optimize automatically' }))
  await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
  expect(vi.mocked(portfolioResearch.submit).mock.calls[0][0].automatic_research).toEqual({
    version: 'automatic-hypotheses-v16',
    history_policy: 'available_history',
    design_policy: 'automatic_design_v1',
    decision_policy: 'automatic_decision_v1',
    action_policy: 'automatic_actions_v1',
  })
  expect(original).toEqual(before)
})

it('does not carry historical-only mode into a previous automatic recipe', async () => {
  const historical = selectPortfolioRun(draft(), true)
  historical.portfolio.automatic_research = {
    version: 'automatic-hypotheses-v16',
    validation_mode: 'historical_only',
  }
  savedDraft(historical)
  mount()
  await userEvent.click(await screen.findByText('Advanced research options'))
  expect(
    screen.queryByRole('checkbox', { name: 'Historical optimisation' })
  ).not.toBeInTheDocument()
  await userEvent.selectOptions(screen.getByLabelText('Research scope'), 'automatic-hypotheses-v15')
  expect(
    screen.queryByRole('checkbox', { name: 'Historical optimisation' })
  ).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Optimize automatically' }))
  await waitFor(() => expect(portfolioResearch.submit).toHaveBeenCalledTimes(1))
  expect(vi.mocked(portfolioResearch.submit).mock.calls[0][0].automatic_research).toEqual({
    version: 'automatic-hypotheses-v15',
  })
})

it.each([
  false,
  true,
])('retains historical qualification on a reopened later-period report (available history %s)', async (availableHistory) => {
  const later: PortfolioResult = {
    ...result,
    summary: { ...result.summary, final_equity: 99800, net_pnl: -200 },
  }
  const combined: PortfolioResult = {
    ...result,
    automatic_research: {
      version: 'automatic-hypotheses-v16',
      status: 'historical',
      independently_validated: false,
      historical_checks_passed: false,
      headline: 'Historical optimisation — not independently validated',
      selected_config_id: 'original',
      selected_is_baseline: true,
      selection_basis: 'Historical comparisons only.',
      recipe: {
        version: 'automatic-hypotheses-v16',
        validation_mode: 'historical_only',
        ...(availableHistory
          ? {
              history_policy: 'available_history' as const,
              history_availability: {
                version: 'research-available-history-v1' as const,
                mode: 'historical_only' as const,
                session_count: 220,
                holding: { max_sessions: 45, min_window_sessions: 46 },
                reason: 'All available signal history is used for historical comparisons.',
                reason_code: 'available_history',
              },
            }
          : {}),
        periods: {
          search: { from: '2026-01-05', to: '2026-01-20' },
          check1: { from: '2026-01-21', to: '2026-01-22' },
          check2: { from: '2026-01-23', to: '2026-01-24' },
          final: { from: '2026-02-01', to: '2026-02-05' },
        },
      },
      baseline: result.summary,
      checks: [],
      final: {
        summary: later.summary,
        baseline_summary: later.summary,
        score_delta: 0,
        supports: false,
      },
      counts: { proposals: 408, simulations: 272, finalists: 1 },
      unsupported_families: [],
    },
    validation: {
      train_from: '2026-01-05',
      train_to: '2026-01-20',
      test_from: '2026-02-01',
      test_to: '2026-02-05',
      training_signals: 20,
      testing_signals: 5,
      label: 'Historical later-period comparison',
      selection_basis: 'Historical comparisons only.',
      result: later,
    },
  }
  const original = structuredClone(combined)
  const view = render(
    <PortfolioResults
      job={{ ...finished, result: combined }}
      result={combined}
      onRerun={vi.fn()}
      rerunning={false}
      exportUrl="/export"
    />
  )
  expect(screen.queryByRole('button', { name: 'Final check', exact: true })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Historical comparison', exact: true }))
  expect(
    screen.getByText(
      availableHistory
        ? 'Historical research · not independently validated'
        : 'Historical optimisation — not independently validated'
    )
  ).toBeVisible()
  expect(
    screen.queryByRole('region', { name: 'Automatic research findings' })
  ).not.toBeInTheDocument()
  expect(
    screen.getByText('Final equity', { selector: 'summary' }).closest('dt')?.parentElement
  ).toHaveTextContent('₹99,800')
  expect(later.automatic_research).toBeUndefined()
  expect(combined).toEqual(original)
  if (availableHistory) {
    view.unmount()
    render(
      <PortfolioResults
        job={{ ...finished, result: combined }}
        result={JSON.parse(JSON.stringify(later))}
        freezeAnalysis
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    )
    expect(screen.getByText(/Using all available signal history · 220 sessions/)).toBeVisible()
    expect(screen.getByText('Historical research · not independently validated')).toBeVisible()
    expect(combined).toEqual(original)
  }
})

it.each([
  [false, 'candidate'],
  [true, 'candidate'],
  [false, 'different-candidate'],
  [true, 'different-candidate'],
] as const)('loads matching frozen parent history and capital scope (frozen %s, setup %s)', async (freezeAnalysis, configId) => {
  const parentId = 'available-history-parent'
  const parent = {
    ...finished,
    id: parentId,
    result: {
      ...result,
      automatic_research: {
        version: 'automatic-hypotheses-v16',
        selected_config_id: 'candidate',
        decision: {
          version: 'research-automatic-decision-v1',
          outcome: 'no_useful_improvement',
          historical_candidate: false,
          independently_validated: false,
          reasons: ['The chosen rules did not survive higher transaction costs'],
          checks: [{ kind: 'higher_costs', passed: false }],
          next_action: 'Retain original rules; review the recorded failure reasons',
        },
        capital_planning: {
          version: 'research-capital-planning-v1',
          status: 'verified_sufficient',
          estimated_capital: 60000,
          verified_capital: 75000,
          original_capital: 100000,
          reference_tickets: [{ strategy_id: 'one', initial_ticket: 3000 }],
          tested_range: [60000, 75000],
          accounts: [
            {
              capital: 75000,
              admitted_requests: 30,
              clipped_requests: 0,
              blockers: {},
              verified_sufficient: true,
            },
          ],
        },
        recipe: {
          version: 'automatic-hypotheses-v16',
          validation_mode: 'exploratory_only',
          history_availability: {
            version: 'research-available-history-v1',
            mode: 'exploratory_only',
            session_count: 160,
            holding: { max_sessions: 82, min_window_sessions: 88 },
            reason: 'The supplied history is the complete dataset.',
            reason_code: 'short_history',
          },
        },
      },
    },
  } as PortfolioJob
  const child = {
    ...result,
    automatic_research: undefined,
    report_context: {
      ...result.report_context,
      config_id: configId,
      period: 'selection',
      parent_job_id: parentId,
      candidate: { study_job_id: parentId, config_id: configId, is_objective_winner: false },
    },
  } as PortfolioResult
  const original = structuredClone(child)
  vi.mocked(portfolioResearch.job).mockResolvedValue(parent)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <PortfolioResults
        job={{ ...finished, id: 'saved-child', result: child }}
        result={JSON.parse(JSON.stringify(child))}
        freezeAnalysis={freezeAnalysis}
        onRerun={vi.fn()}
        rerunning={false}
        exportUrl="/export"
      />
    </QueryClientProvider>
  )
  expect(await screen.findByText(/Using all available signal history · 160 sessions/)).toBeVisible()
  expect(screen.getByText('Historical research · no independent later-period check')).toBeVisible()
  if (configId === 'candidate') {
    expect(screen.getByText('Capital verified for this history: ₹75,000')).toBeVisible()
    expect(screen.getByRole('region', { name: 'Research decision' })).toBeVisible()
    expect(
      screen.getByText('The chosen rules did not survive higher transaction costs')
    ).toBeVisible()
  } else {
    expect(
      screen.queryByRole('region', { name: 'Starting capital estimate' })
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Research decision' })).not.toBeInTheDocument()
  }
  expect(portfolioResearch.job).toHaveBeenCalledWith(parentId, expect.any(AbortSignal))
  expect(child).toEqual(original)
})
