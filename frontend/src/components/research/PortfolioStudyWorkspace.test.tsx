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
import { researchDefaults } from '@/lib/researchDraft'
import { useAuthStore } from '@/stores/authStore'
import { PortfolioStudyWorkspace } from './PortfolioStudyWorkspace'

vi.mock('@/api/researchCandidates', () => ({
  researchCandidates: { get: vi.fn(), prepare: vi.fn() },
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
  options: { readOnly?: boolean; data?: PortfolioResult; onOpenReport?: (id: string) => void } = {}
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  const onOpenReport = options.onOpenReport ?? vi.fn()
  const onRerun = vi.fn()
  const tree = (
    <QueryClientProvider client={client}>
      <PortfolioStudyWorkspace
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
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
})

describe('connected study exploration', () => {
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
