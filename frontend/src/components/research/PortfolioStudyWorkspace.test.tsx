import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  type AnalysisChart,
  type PortfolioJob,
  type PortfolioResult,
  portfolioResearch,
} from '@/api/portfolioResearch'
import { type CandidateReportsReceipt, researchCandidates } from '@/api/researchCandidates'
import { researchShortlist, type ShortlistCandidate } from '@/api/researchShortlist'
import { researchStudyActivity } from '@/api/researchStudyActivity'
import { researchDefaults } from '@/lib/researchDraft'
import { useAuthStore } from '@/stores/authStore'
import { PortfolioStudyWorkspace } from './PortfolioStudyWorkspace'

vi.mock('@/api/researchCandidates', () => ({
  researchCandidates: { get: vi.fn(), prepare: vi.fn() },
}))
vi.mock('@/api/researchStudyActivity', () => ({
  researchStudyActivity: { get: vi.fn() },
}))
vi.mock('@/api/researchShortlist', async (original) => ({
  ...(await original<typeof import('@/api/researchShortlist')>()),
  researchShortlist: { list: vi.fn(), save: vi.fn() },
}))
vi.mock('@/api/portfolioResearch', () => ({
  portfolioResearch: {
    analysis: vi.fn().mockResolvedValue({ status: 'missing' }),
    analysisExportUrl: () => '/analysis',
    prepareAnalysis: vi.fn(),
  },
}))
vi.mock('./AnalysisCharts', () => ({
  AnalysisFigure: ({
    chart,
    onPoint,
  }: {
    chart: AnalysisChart
    onPoint?: (point: { customdata: unknown }) => void
  }) => (
    <button
      type="button"
      onClick={() =>
        onPoint?.({
          customdata: Array.isArray(chart.figure?.data[0].customdata)
            ? chart.figure.data[0].customdata[0]
            : null,
        })
      }
    >
      Point in {chart.id}
    </button>
  ),
}))
const studyId = 'study-job'
function result(): PortfolioResult {
  const rows = [0, 1, 2].map((number) => ({
    config_id: `config-${number}`,
    trial_number: number,
    score: 3 - number,
    stage: 'tpe',
    strategies: [
      {
        id: 's',
        name: 'Breakout',
        allocation_pct: 100,
        config: { ...researchDefaults, target_pct: number + 1 },
      },
    ],
    summary: { net_return_pct: 5 - number, max_drawdown_pct: 2, closed_trades: 10 },
  }))
  return {
    config: { initial_capital: 100000 },
    strategies: rows[0].strategies,
    summary: rows[0].summary,
    equity_curve: [],
    ledger: [],
    per_strategy: [],
    experiment: {
      kind: 'portfolio_optimize',
      rows,
      recommendation_id: 'config-0',
      selected_strategies: rows[0].strategies,
      optimizer: {
        sampler: 'TPESampler',
        objective_definition: 'Return less drawdown',
        version: '4.0',
      },
      specification: { sampler: 'tpe', trials: 5, seed: 0, objective: 'balanced' },
      counts: { remaining: 999 },
      search_space: {
        axes: {
          's.target_pct': { min: 1, max: 3, step: 1 },
          's.stop_pct': { min: 1, max: 3, step: 1 },
        },
      },
      trials: rows
        .map((row) => ({
          number: row.trial_number,
          config_id: row.config_id,
          state: 'complete' as const,
          value: row.score,
          params: { 's.target_pct': row.trial_number + 1, 's.stop_pct': 1 },
          reused: false,
        }))
        .concat([
          {
            number: 3,
            config_id: 'config-1',
            state: 'complete',
            value: 2,
            params: { 's.target_pct': 2, 's.stop_pct': 1 },
            reused: true,
          },
        ]),
      study_analysis: {
        version: 'research-analysis-v1',
        basis: ['All recorded proposals'],
        parameters: ['s.target_pct', 's.stop_pct'],
        charts: [
          {
            id: 'history',
            title: 'Optimization history',
            status: 'available',
            figure: { data: [{ type: 'scatter', mode: 'markers', x: [1], y: [2] }], layout: {} },
          },
          {
            id: 'contour',
            title: 'Contour',
            status: 'available',
            figure: {
              data: [{ type: 'scatter', mode: 'markers', x: [2], y: [1] }],
              layout: {
                xaxis: { title: { text: 's.target_pct' } },
                yaxis: { title: { text: 's.stop_pct' } },
              },
            },
          },
          {
            id: 'timeline',
            title: 'Timeline',
            status: 'unavailable',
            reason: 'Timing was not recorded.',
          },
        ],
      },
    },
  }
}
const receipt = (): CandidateReportsReceipt => ({
  version: 'research-candidate-reports-v1',
  study_job_id: studyId,
  period: 'full',
  candidates: [0, 1, 2].map((i) => ({
    config_id: `config-${i}`,
    trial_number: i,
    is_objective_winner: i === 0,
    status: i === 0 ? 'ready' : 'available',
    ...(i === 0 ? { report_job_id: studyId } : {}),
  })),
})
const clients: QueryClient[] = []
function mount(
  options: {
    readOnly?: boolean
    data?: PortfolioResult
    onOpenReport?: (id: string) => void
    experimentId?: string
  } = {}
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  const onOpenReport = options.onOpenReport ?? vi.fn()
  const onRerun = vi.fn()
  const tree = (
    <QueryClientProvider client={client}>
      <PortfolioStudyWorkspace
        experimentId={options.experimentId}
        job={
          {
            id: studyId,
            status: 'completed',
            progress: 100,
            created_at: '2026-01-01',
          } as PortfolioJob
        }
        result={options.data ?? result()}
        readOnly={options.readOnly ?? false}
        rerunning={false}
        onRerun={onRerun}
        onAdjust={vi.fn()}
        onOpenReport={onOpenReport}
        renderSettings={(strategy) => <p>Target {strategy.config.target_pct}%</p>}
      />
    </QueryClientProvider>
  )
  return { ...render(tree), onOpenReport, onRerun }
}
beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  localStorage.clear()
  useAuthStore.setState({ user: { username: 'test-owner' } })
  vi.mocked(researchCandidates.get).mockResolvedValue(receipt())
  vi.mocked(researchShortlist.list).mockResolvedValue({
    version: 'research-shortlist-v1',
    experiment_id: 'saved-experiment',
    archived: false,
    items: [],
    total: 0,
    next_offset: null,
  })
  vi.mocked(researchShortlist.save).mockResolvedValue({
    candidate: { id: 'saved-candidate' } as ShortlistCandidate,
    reused: false,
  })
  vi.mocked(researchStudyActivity.get).mockResolvedValue({
    version: 'research-study-activity-v1',
    job_id: studyId,
    job_status: 'completed',
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
  cleanup()
  for (const client of clients.splice(0)) client.clear()
})

describe('connected study exploration', () => {
  it('opens a retained baseline report even when automatic checks select no searched candidate', async () => {
    const data = result()
    data.experiment!.objective_winner_id = 'config-0'
    data.experiment!.recommendation_id = 'baseline-outside-search'
    data.automatic_research = {
      version: 'automatic-trade-management-v1',
      status: 'not_supported',
      headline: 'No reliable improvement over unchanged settings',
      selected_config_id: 'baseline-outside-search',
      selected_is_baseline: true,
      selection_basis: 'Original settings retained after later checks.',
      recipe: {
        version: 'automatic-trade-management-v1',
        periods: {
          search: { from: '2026-01-01', to: '2026-04-30' },
          check1: { from: '2026-05-01', to: '2026-05-31' },
          check2: { from: '2026-06-01', to: '2026-06-30' },
          final: { from: '2026-07-01', to: '2026-08-31' },
        },
      },
      baseline: {},
      checks: [],
      final: null,
      counts: { proposals: 50, simulations: 61, finalists: 3 },
      unsupported_families: [],
    }
    const { onOpenReport, onRerun } = mount({ data, experimentId: 'saved-experiment' })
    expect(screen.getByRole('region', { name: 'Automatic research findings' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Best report' })).not.toBeInTheDocument()
    expect(screen.getByText('Best objective score').parentElement).toHaveTextContent('3')
    expect(researchCandidates.get).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Add trials' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Backtest these settings' }))
    expect(onRerun).toHaveBeenCalledWith()
    await userEvent.click(screen.getByRole('button', { name: 'Selected settings report' }))
    expect(onOpenReport).toHaveBeenCalledWith(studyId)
    await userEvent.click(screen.getByRole('button', { name: 'Point in history' }))
    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).queryByRole('button', { name: /Save|Prepare report|Adjust & test/ })
    ).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Backtest these settings' }))
    expect(onRerun).toHaveBeenLastCalledWith('config-1')
  })

  it('loads durable activity only on the Activity tab and retains the native timeline', async () => {
    mount()
    expect(researchStudyActivity.get).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('tab', { name: 'Activity', exact: true }))
    expect(
      await screen.findByText('Detailed activity was not recorded for this study.')
    ).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Timeline' })).toBeVisible()
    expect(screen.getByText('Timing was not recorded.')).toBeVisible()
    expect(researchStudyActivity.get).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('tab', { name: 'Overview', exact: true }))
    expect(
      screen.queryByText('Detailed activity was not recorded for this study.')
    ).not.toBeInTheDocument()
  })

  it('aborts an unfinished activity lookup on leaving its tab', async () => {
    vi.mocked(researchStudyActivity.get).mockImplementation(() => new Promise(() => {}))
    mount()
    await userEvent.click(screen.getByRole('tab', { name: 'Activity', exact: true }))
    const signal = vi.mocked(researchStudyActivity.get).mock.calls[0][2]!
    await userEvent.click(screen.getByRole('tab', { name: 'Overview', exact: true }))
    expect(signal.aborted).toBe(true)
  })

  it('uses actual proposal counts, opens a plotted proposal in place and keeps preparation explicit', async () => {
    const data = result()
    data.experiment!.specification.trials = 100
    data.experiment!.search_space!.proposal_budget = 5
    const view = mount({ data })
    expect(screen.getByText('4 / 5')).toBeVisible()
    expect(screen.queryByText('999')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Point in history' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Trial 2' })).toBeVisible()
    expect(within(dialog).getByText('Target 2%')).toBeVisible()
    expect(await within(dialog).findByRole('button', { name: 'Prepare report' })).toBeEnabled()
    expect(researchCandidates.prepare).not.toHaveBeenCalled()
    expect(portfolioResearch.prepareAnalysis).not.toHaveBeenCalled()
    expect(view.onRerun).not.toHaveBeenCalled()
    await act(async () => {
      expect((await axe(document.body)).violations).toEqual([])
    })
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Point in history' })).toHaveFocus()
  })

  it('queues one exact report and opens the durable result without replaying the study', async () => {
    const next = receipt()
    const view = mount()
    await userEvent.click(screen.getByRole('button', { name: 'Point in history' }))
    await screen.findByRole('button', { name: 'Prepare report' })
    vi.mocked(researchCandidates.prepare).mockImplementation(async () => {
      next.candidates[1] = {
        ...next.candidates[1],
        status: 'ready',
        report_job_id: 'candidate-job',
      }
      return { ...next.candidates[1], status: 'queued' }
    })
    vi.mocked(researchCandidates.get).mockResolvedValue(next)
    await userEvent.dblClick(screen.getByRole('button', { name: 'Prepare report' }))
    expect(researchCandidates.prepare).toHaveBeenCalledTimes(1)
    expect(researchCandidates.prepare).toHaveBeenCalledWith(
      studyId,
      'config-1',
      expect.any(AbortSignal)
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Open report', exact: true }))
    expect(view.onOpenReport).toHaveBeenCalledWith('candidate-job')
    expect(view.onRerun).not.toHaveBeenCalled()
  })

  it('keeps winner available when availability cannot load and allows read-only report viewing', async () => {
    vi.mocked(researchCandidates.get).mockRejectedValue(new Error('offline'))
    const view = mount({ readOnly: true })
    await userEvent.click(screen.getByRole('button', { name: 'Best report' }))
    expect(view.onOpenReport).toHaveBeenCalledWith(studyId)
    await userEvent.click(screen.getByRole('button', { name: 'Point in history' }))
    expect(screen.queryByRole('button', { name: 'Prepare report' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Adjust & test' })).not.toBeInTheDocument()
    expect(await screen.findByText('Report availability could not be checked.')).toBeVisible()
  })

  it('asks which actual proposal to inspect at overlapping contour observations', async () => {
    mount()
    await userEvent.click(screen.getByRole('tab', { name: 'Parameters' }))
    await userEvent.click(screen.getByRole('button', { name: 'Point in contour' }))
    expect(screen.getByRole('heading', { name: 'Trials at this point' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: /Trial 4/ }))
    expect(screen.getByRole('heading', { name: 'Trial 4' })).toBeVisible()
    expect(
      screen.getByText(/Repeated proposal · uses the portfolio first tested in Trial 2/)
    ).toBeVisible()
    expect(screen.getByText('Target 2%')).toBeVisible()
  })

  it('restores table scope and exact selected proposal after returning to the study', async () => {
    const first = mount()
    await userEvent.click(screen.getByRole('tab', { name: 'Trials' }))
    await userEvent.click(screen.getByRole('button', { name: 'All proposals' }))
    const row = screen.getAllByRole('row').find((item) => item.textContent?.includes('Reused'))!
    await userEvent.click(within(row).getByRole('button', { name: 'View details' }))
    expect(screen.getByRole('heading', { name: 'Trial 4' })).toBeVisible()
    first.unmount()
    mount()
    expect(screen.getByRole('heading', { name: 'Trial 4' })).toBeVisible()
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('tab', { name: 'Trials', exact: true })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByRole('button', { name: 'All proposals' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })
  it('shortlists the actually inspected repeated proposal without preparing a report', async () => {
    mount({ experimentId: 'saved-experiment' })
    await userEvent.click(screen.getByRole('tab', { name: 'Trials' }))
    await userEvent.click(screen.getByRole('button', { name: 'All proposals' }))
    const row = screen.getAllByRole('row').find((item) => item.textContent?.includes('Reused'))!
    await userEvent.click(within(row).getByRole('button', { name: 'View details' }))
    const button = await screen.findByRole('button', { name: 'Shortlist', exact: true })
    await userEvent.click(button)
    expect(researchShortlist.save).toHaveBeenCalledWith(
      'saved-experiment',
      { job_id: studyId, config_id: 'config-1', proposal_number: 3 },
      expect.any(AbortSignal)
    )
    expect(researchCandidates.prepare).not.toHaveBeenCalled()
    expect(result().experiment?.recommendation_id).toBe('config-0')
  })

  it('shows a reconstruction mismatch without offering a new report as if it matched', async () => {
    const data = receipt()
    data.candidates[1] = {
      ...data.candidates[1],
      status: 'mismatch',
      report_job_id: 'failed-child',
      error: 'The saved study statistics did not match.',
    }
    vi.mocked(researchCandidates.get).mockResolvedValue(data)
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Point in history' }))
    expect(await screen.findByText('The saved study statistics did not match.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Prepare report' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Open report', exact: true })
    ).not.toBeInTheDocument()
  })

  it('aborts report preparation when the workspace is unmounted', async () => {
    vi.mocked(researchCandidates.prepare).mockImplementation(() => new Promise(() => {}))
    const view = mount()
    await userEvent.click(screen.getByRole('button', { name: 'Point in history' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Prepare report' }))
    const signal = vi.mocked(researchCandidates.prepare).mock.calls[0][2]!
    view.unmount()
    expect(signal.aborted).toBe(true)
  })
})
