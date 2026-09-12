import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError } from 'axios'
import { axe } from 'jest-axe'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  type ComparisonMember,
  researchComparisons,
  type SavedComparison,
} from '@/api/researchComparisons'
import { researchDecisions } from '@/api/researchDecisions'
import { useAuthStore } from '@/stores/authStore'
import { PortfolioResults } from './PortfolioResults'
import {
  comparisonChartView,
  comparisonMetricText,
  ResearchComparisons,
} from './ResearchComparisons'

vi.mock('@/api/researchComparisons', async (original) => ({
  ...(await original<typeof import('@/api/researchComparisons')>()),
  researchComparisons: { list: vi.fn(), get: vi.fn(), member: vi.fn(), update: vi.fn() },
}))
vi.mock('./PortfolioResults', () => ({
  PortfolioResults: vi.fn(() => <div>Exact frozen report</div>),
}))
vi.mock('@/api/researchDecisions', async (original) => ({
  ...(await original<typeof import('@/api/researchDecisions')>()),
  researchDecisions: { context: vi.fn(), opened: vi.fn() },
}))
vi.mock('./AnalysisCharts', () => ({
  AnalysisFigure: ({ chart }: { chart: unknown }) => (
    <output aria-label="Saved comparison curve">{JSON.stringify(chart)}</output>
  ),
}))
function member(id: string, name: string): ComparisonMember {
  return {
    id,
    name,
    note: '',
    origin_kind: 'study',
    source_job_id: 'study',
    source_result_artifact: 'source',
    config_id: id,
    period: 'selection',
    trial_number: 1,
    proposal_number: 3,
    is_objective_winner: false,
    report_job_id: 'report',
    report_result_artifact: `result-${id}`,
    analysis_job_id: null,
    analysis_artifact: null,
    report_context: {
      version: 'research-report-context-v1',
      report_id: id,
      job_id: 'report',
      result_artifact: `result-${id}`,
      inputs_artifact: 'inputs',
      config_id: id,
      period: 'selection',
      period_label: 'Selection period',
      dates: { from: '2026-01-01', to: '2026-06-01' },
      analysis_version: 'v2',
      analysis_artifact: null,
      evaluation_basis: { version: 'research-evaluation-basis-v1', status: 'unverified' },
    },
    summary: {},
    available: true,
  }
}
function saved(overrides: Partial<SavedComparison> = {}): SavedComparison {
  return {
    id: 'c',
    experiment_id: 'e',
    number: 1,
    name: 'Breakout · Comparison 1',
    note: '',
    revision: 1,
    created_at: 1,
    updated_at: 1,
    archived: false,
    member_count: 2,
    member_names: ['Reference report', 'Candidate report'],
    reference_member_id: 'a',
    compatible: true,
    currency: 'INR',
    version: 'research-comparison-v1',
    members: [member('a', 'Reference report'), member('b', 'Candidate report')],
    differences: [],
    metrics: [
      {
        key: 'net_return_pct',
        label: 'Net return',
        format: 'percent',
        source: 'summary',
        description: 'Saved account return.',
        values: { a: 10, b: 13 },
        deltas: { a: 0, b: 3 },
        unavailable: {},
      },
      {
        key: 'sharpe',
        label: 'Sharpe ratio',
        format: 'number',
        source: 'analysis',
        description: 'Saved ratio.',
        values: { a: 1, b: 2 },
        deltas: { a: 0, b: 1 },
        unavailable: {},
      },
    ],
    cumulative: {
      id: 'comparison-returns',
      title: 'Cumulative return',
      status: 'available',
      figure: { data: [{ name: 'a', x: [1, 2], y: [2, 3] }], layout: {} },
    },
    ...overrides,
  }
}
const clients: QueryClient[] = []
function Location() {
  return <output data-testid="url">{useLocation().search}</output>
}
function mount(url = '/?experiment=e&view=comparisons&comparison=c', readOnly = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <ResearchComparisons experimentId="e" readOnly={readOnly} />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(researchDecisions.opened).mockResolvedValue({ opened_at: 1, reused: false })
  useAuthStore.setState({ user: { username: 'owner' } })
  vi.mocked(researchComparisons.get).mockResolvedValue(saved())
  vi.mocked(researchComparisons.list).mockResolvedValue({
    items: [saved()],
    total: 21,
    next_offset: 20,
  })
  vi.mocked(researchComparisons.member).mockResolvedValue({
    member: member('b', 'Candidate report'),
    available: true,
    job: { id: 'report', progress: 100, status: 'completed', created_at: 1 },
    result: {
      summary: { net_return_pct: 13 },
      config: { initial_capital: 100 },
      strategies: [],
      per_strategy: [],
      ledger: [],
      equity_curve: [],
    },
  })
  vi.mocked(researchComparisons.update).mockImplementation(async (_e, _id, patch) =>
    saved({ ...patch, revision: patch.revision + 1 })
  )
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
})
describe('saved comparisons journey', () => {
  it('places the legend below the plot without changing the saved chart or coordinates', () => {
    const chart = saved().cumulative
    const original = JSON.stringify(chart)
    Object.freeze(chart.figure!.data)
    Object.freeze(chart.figure!.layout)
    Object.freeze(chart.figure!)
    Object.freeze(chart)
    const displayed = comparisonChartView(chart)
    expect(displayed.figure!.data).toBe(chart.figure!.data)
    expect(displayed.figure!.layout.legend).toMatchObject({
      orientation: 'h',
      xanchor: 'left',
      yanchor: 'top',
    })
    expect((displayed.figure!.layout.legend as { y: number }).y).toBeLessThan(0)
    expect(JSON.stringify(chart)).toBe(original)
  })
  it('shows original values with neutral percentage-point deltas and collapsed advanced statistics', async () => {
    mount()
    await screen.findByRole('heading', { name: 'Breakout · Comparison 1' })
    expect(screen.getByText('13%')).toBeVisible()
    expect(screen.getByLabelText('Net return difference for Candidate report')).toHaveTextContent(
      '+3 pp'
    )
    expect(screen.getByText('Sharpe ratio')).not.toBeVisible()
    await userEvent.click(screen.getByText('More statistics', { selector: 'summary' }))
    expect(screen.getByText('Sharpe ratio')).toBeVisible()
    await userEvent.type(screen.getByLabelText('Find comparison statistic'), 'unknown')
    expect(screen.getByText('No saved statistics match.')).toBeVisible()
    expect(screen.getByLabelText('Saved comparison curve')).toHaveTextContent('"y":[2,3]')
    expect(screen.getByRole('heading', { name: 'Cumulative return' })).toBeVisible()
    expect(researchComparisons.member).not.toHaveBeenCalled()
    expect(researchDecisions.context).not.toHaveBeenCalled()
    expect(researchDecisions.opened).not.toHaveBeenCalled()
    await act(async () => {
      expect((await axe(document.body)).violations).toEqual([])
    })
  })
  it('hides all deltas and overlay for unmatched inputs while retaining inspectable reports', async () => {
    vi.mocked(researchComparisons.get).mockResolvedValue(
      saved({ compatible: false, differences: [{ member_id: 'b', codes: ['capital', 'period'] }] })
    )
    mount()
    expect(await screen.findByText(/Inspection only/)).toBeVisible()
    expect(screen.getByText('Candidate report: Starting capital, Evaluation period')).toBeVisible()
    expect(screen.getByText('13%')).toBeVisible()
    expect(
      screen.queryByLabelText('Net return difference for Candidate report')
    ).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Saved comparison curve')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open report for Candidate report' })).toBeEnabled()
  })
  it('opens an exact frozen member and returns to the same reference and selection through the URL', async () => {
    mount(
      '/?experiment=e&view=comparisons&comparison=c&compare_candidates=a,b&compare_reference=a&shortlist_offset=20'
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Open report for Candidate report' })
    )
    await screen.findByText('Exact frozen report')
    await waitFor(() => expect(researchDecisions.opened).toHaveBeenCalledTimes(1))
    expect(researchDecisions.opened).toHaveBeenLastCalledWith(
      'e',
      {
        request_id: expect.any(String),
        target: { kind: 'comparison_member', comparison_id: 'c', member_id: 'b' },
      },
      expect.any(AbortSignal)
    )
    expect(researchComparisons.member).toHaveBeenCalledWith('e', 'c', 'b', expect.any(AbortSignal))
    const props = vi.mocked(PortfolioResults).mock.calls.at(-1)![0]
    expect(props).toMatchObject({
      freezeAnalysis: true,
      readOnly: true,
      embedded: true,
      exportUrl: '',
      result: { summary: { net_return_pct: 13 } },
    })
    expect(screen.getByTestId('url')).toHaveTextContent('comparison_member=b')
    await userEvent.click(screen.getByRole('button', { name: 'Back to comparison' }))
    await screen.findByRole('heading', { name: 'Breakout · Comparison 1' })
    expect(screen.getByTestId('url')).not.toHaveTextContent('comparison_member')
    expect(screen.getByTestId('url')).toHaveTextContent('compare_reference=a')
    await userEvent.click(screen.getByRole('button', { name: 'Back to shortlist' }))
    expect(screen.getByTestId('url')).toHaveTextContent('view=shortlist')
    expect(screen.getByTestId('url')).toHaveTextContent('shortlist_offset=20')
  })
  it('reopens a frozen member directly after reload without looking up latest report or comparison state', async () => {
    mount('/?experiment=e&view=comparisons&comparison=c&comparison_member=b')
    await screen.findByText('Exact frozen report')
    expect(researchComparisons.get).not.toHaveBeenCalled()
    expect(researchComparisons.list).not.toHaveBeenCalled()
    expect(researchComparisons.member).toHaveBeenCalledTimes(1)
    expect(researchDecisions.opened).not.toHaveBeenCalled()
  })
  it('returns from the original comparison to its exact decision event', async () => {
    mount(
      '/?experiment=e&view=comparisons&comparison=c&return_decision=d&return_decision_event=old-event&decision_offset=20'
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Back to decision' }))
    expect(screen.getByTestId('url')).toHaveTextContent('view=decisions')
    expect(screen.getByTestId('url')).toHaveTextContent('decision_event=old-event')
    expect(screen.getByTestId('url')).toHaveTextContent('decision_offset=20')
    expect(researchDecisions.opened).not.toHaveBeenCalled()
  })
  it('preserves the draft note on a revision conflict until explicit reload', async () => {
    const error = new AxiosError('conflict')
    error.response = {
      status: 409,
      data: { code: 'comparison_revision_conflict', message: 'Changed in another tab.' },
    } as AxiosError['response']
    vi.mocked(researchComparisons.update).mockRejectedValueOnce(error)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Edit name & note' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit comparison' })
    await userEvent.type(within(dialog).getByLabelText('Note'), 'My unsaved reasoning')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Changed in another tab.')
    expect(within(dialog).getByLabelText('Note')).toHaveValue('My unsaved reasoning')
    expect(within(dialog).getByRole('button', { name: 'Save changes' })).toBeDisabled()
    vi.mocked(researchComparisons.get).mockResolvedValue(
      saved({ note: 'Other tab note', revision: 4 })
    )
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reload saved details' }))
    await waitFor(() => expect(within(dialog).getByLabelText('Note')).toHaveValue('Other tab note'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    await waitFor(() =>
      expect(researchComparisons.update).toHaveBeenLastCalledWith(
        'e',
        'c',
        { revision: 4, name: 'Breakout · Comparison 1', note: 'Other tab note' },
        expect.any(AbortSignal)
      )
    )
  })
  it('keeps archive read-only and missing pinned evidence unavailable', async () => {
    vi.mocked(researchComparisons.get).mockResolvedValue(
      saved({
        archived: true,
        members: [
          member('a', 'Reference report'),
          {
            ...member('b', 'Candidate report'),
            available: false,
            error: 'Original analysis is missing.',
          },
        ],
      })
    )
    mount()
    await screen.findByRole('heading', { name: 'Breakout · Comparison 1' })
    expect(screen.queryByRole('button', { name: 'Edit name & note' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open report for Candidate report' })).toBeDisabled()
    expect(screen.getByText('Original analysis is missing.')).toBeVisible()
    expect(screen.queryByRole('button', { name: /Delete|Remove/ })).not.toBeInTheDocument()
  })
  it('pages bounded metadata only and aborts the scoped request on close', async () => {
    const view = mount('/?experiment=e&view=comparisons')
    await screen.findByRole('button', { name: 'Breakout · Comparison 1' })
    vi.mocked(researchComparisons.list).mockImplementationOnce(() => new Promise(() => undefined))
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() =>
      expect(researchComparisons.list).toHaveBeenLastCalledWith('e', 20, expect.any(AbortSignal))
    )
    view.unmount()
    expect(vi.mocked(researchComparisons.list).mock.calls.at(-1)![2]!.aborted).toBe(true)
    expect(researchComparisons.get).not.toHaveBeenCalled()
  })
  it('never invents currency or treats undefined ratios as zero', () => {
    const metric = { ...saved().metrics[0], format: 'money' as const }
    expect(comparisonMetricText(metric, 1000, null)).toBe('—')
    expect(comparisonMetricText(metric, null, 'INR')).toBe('—')
    expect(comparisonMetricText(metric, 1000, 'USD')).toContain('$')
    expect(comparisonMetricText(saved().metrics[0], Number.NaN, 'INR', true)).toBe('—')
  })
})
