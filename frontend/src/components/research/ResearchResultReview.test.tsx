import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PortfolioJob, PortfolioResult } from '@/api/portfolioResearch'
import { researchDecisions } from '@/api/researchDecisions'
import { researchValidation, type ValidationContext } from '@/api/researchValidation'
import { useAuthStore } from '@/stores/authStore'
import { CandidateDecision } from './CandidateDecision'
import { ResearchResultReview } from './ResearchResultReview'

vi.mock('@/api/researchValidation', async (original) => ({
  ...(await original<typeof import('@/api/researchValidation')>()),
  researchValidation: { context: vi.fn(), prepare: vi.fn() },
}))
vi.mock('@/api/researchDecisions', async (original) => ({
  ...(await original<typeof import('@/api/researchDecisions')>()),
  researchDecisions: { preview: vi.fn(), opened: vi.fn() },
}))
vi.mock('./CandidateDecision', () => ({
  CandidateDecision: vi.fn(() => <button type="button">Decision</button>),
}))
vi.mock('./PortfolioResults', () => ({ PortfolioResults: () => <div>Frozen later report</div> }))

const job: PortfolioJob = {
  id: 'prepared-alternative',
  status: 'completed',
  progress: 100,
  created_at: 1,
}
const result = {
  summary: {},
  config: {},
  strategies: [],
  per_strategy: [],
  ledger: [],
  equity_curve: [],
  report_context: {
    config_id: 'alternative',
    report_id: 'report',
    period: 'selection',
    result_artifact: 'rendered-artifact',
    analysis_artifact: null,
  },
} as unknown as PortfolioResult
const context = (patch: Partial<ValidationContext> = {}): ValidationContext => ({
  version: '1',
  experiment_id: 'idea',
  revision: 4,
  archived: false,
  candidate: {
    source_job_id: 'original-study',
    source_result_artifact: 'frozen-artifact',
    config_id: 'alternative',
    trial_number: 7,
    is_objective_winner: false,
    report_job_id: job.id,
  },
  selection: { from: '2026-01-21', to: '2026-06-30' },
  reservation: { from: '2026-07-01', to: '2026-09-10' },
  evidence_use: {
    reservation: null,
    reservation_status: 'reserved_in_setup',
    calculation: 'not_recorded',
    opened_at: null,
    later_used_for_decision: false,
    coverage: 'legacy_unknown',
    overlap: 'not_found',
  },
  current: null,
  evaluations: [],
  action: { kind: 'prepare' },
  ...patch,
})
const later = {
  job_id: 'later-job',
  status: 'completed' as const,
  progress: 100,
  evidence_id: 'exact-later',
  identity: {
    result_artifact: 'rendered-artifact',
    analysis_artifact: null,
    config_id: 'alternative',
  },
  view: 'primary' as const,
}
const clients: QueryClient[] = []
function Location() {
  const location = useLocation()
  return (
    <>
      <output aria-label="Location">{location.search}</output>
      <output aria-label="Navigation state">{JSON.stringify(location.state)}</output>
    </>
  )
}
function mount(
  options: {
    currentJob?: PortfolioJob
    currentResult?: PortfolioResult
    readOnly?: boolean
    state?: unknown
  } = {}
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/scanner-research',
            search: '?experiment=idea&view=backtests&job=prepared-alternative&return_research=old',
            state: options.state,
          },
        ]}
      >
        <ResearchResultReview
          experimentId="idea"
          job={options.currentJob ?? job}
          result={options.currentResult ?? result}
          readOnly={options.readOnly ?? false}
        />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { username: 'owner' } })
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  vi.mocked(researchValidation.context).mockResolvedValue(context())
  vi.mocked(researchValidation.prepare).mockResolvedValue({
    job: { ...job, id: 'later-job', status: 'queued' },
    reused: false,
    context: context(),
  })
  vi.mocked(researchDecisions.opened).mockResolvedValue({ opened_at: 10, reused: false })
  vi.mocked(researchDecisions.preview).mockResolvedValue({
    available: true,
    job: { ...job, id: 'later-job' },
    result,
  })
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
})
describe('connected result validation', () => {
  it('reads without starting work, then reviews and queues the exact original candidate', async () => {
    mount()
    await screen.findByRole('button', { name: 'Test later period' })
    expect(researchValidation.prepare).not.toHaveBeenCalled()
    expect(researchDecisions.opened).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Test later period' }))
    expect(await screen.findByText('Trial 8')).toBeVisible()
    expect(
      screen.getByText('Saved prices and rules. Fresh starting cash; no positions carried over.')
    ).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Run later test' }))
    await waitFor(() =>
      expect(researchValidation.prepare).toHaveBeenCalledWith(
        'idea',
        { job_id: job.id, config_id: 'alternative' },
        {
          revision: 4,
          source_result_artifact: 'frozen-artifact',
          request_id: expect.any(String),
        },
        expect.any(AbortSignal)
      )
    )
    const params = new URLSearchParams(screen.getByLabelText('Location').textContent!)
    expect(params.get('job')).toBe('later-job')
    expect(new URLSearchParams(params.get('return_research')!).get('return_research')).toBeNull()
    expect(screen.getByLabelText('Navigation state')).toHaveTextContent('researchValidationOpen')
    expect(researchDecisions.opened).not.toHaveBeenCalled()
  })
  it('reopens a completed calculation without another job and retains the candidate in the decision', async () => {
    vi.mocked(researchValidation.context).mockResolvedValue(
      context({ evaluations: [later], action: { kind: 'open', ...later } })
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Review later test' }))
    await userEvent.click(screen.getByRole('button', { name: 'Open saved report' }))
    expect(screen.getByLabelText('Location')).toHaveTextContent('job=later-job')
    expect(researchValidation.prepare).not.toHaveBeenCalled()
    expect(vi.mocked(CandidateDecision).mock.calls.at(-1)![0].target).toEqual({
      job_id: job.id,
      config_id: 'alternative',
      expected_report: { result_artifact: 'rendered-artifact', analysis_artifact: null },
    })
  })
  it('retries a failed request with its original token and aborts when closed', async () => {
    vi.mocked(researchValidation.prepare).mockRejectedValueOnce(new Error('temporary'))
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Test later period' }))
    await userEvent.click(screen.getByRole('button', { name: 'Run later test' }))
    await screen.findByRole('alert')
    vi.mocked(researchValidation.prepare).mockImplementationOnce(() => new Promise(() => {}))
    await userEvent.click(screen.getByRole('button', { name: 'Run later test' }))
    const calls = vi.mocked(researchValidation.prepare).mock.calls
    expect(calls[1][2]).toEqual(calls[0][2])
    await userEvent.keyboard('{Escape}')
    expect(calls[1][3]?.aborted).toBe(true)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  it('keeps archived reports readable but disables a new calculation', async () => {
    mount({ readOnly: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Test later period' }))
    expect(screen.getByRole('button', { name: 'Run later test' })).toBeDisabled()
    expect(researchValidation.prepare).not.toHaveBeenCalled()
  })
  it('cancels an unmounted context without recording an opening', async () => {
    vi.mocked(researchValidation.context).mockImplementation(() => new Promise(() => {}))
    const mounted = mount()
    await waitFor(() => expect(researchValidation.context).toHaveBeenCalledTimes(1))
    const signal = vi.mocked(researchValidation.context).mock.calls[0][2]!
    mounted.unmount()
    expect(signal.aborted).toBe(true)
    expect(researchDecisions.opened).not.toHaveBeenCalled()
  })
  it.each([
    undefined,
    { researchValidationOpen: { jobId: 'different-job', experimentId: 'idea', requestId: 'open' } },
  ])('does not mark a later report opened on a bare or mismatched URL', async (state) => {
    vi.mocked(researchValidation.context).mockResolvedValue(
      context({ evaluations: [later], action: { kind: 'open', ...later } })
    )
    mount({
      currentJob: { ...job, id: later.job_id },
      currentResult: {
        ...result,
        report_context: { ...result.report_context!, period: 'evaluation' },
      },
      state,
    })
    await screen.findByRole('button', { name: 'Selection report' })
    expect(researchDecisions.opened).not.toHaveBeenCalled()
  })
  it('records explicit later viewing against its exact source and returns to the nonwinner selection report', async () => {
    vi.mocked(researchValidation.context).mockResolvedValue(
      context({ evaluations: [later], action: { kind: 'open', ...later } })
    )
    mount({
      currentJob: { ...job, id: later.job_id },
      currentResult: {
        ...result,
        report_context: { ...result.report_context!, period: 'evaluation' },
      },
      state: {
        researchValidationOpen: {
          jobId: later.job_id,
          experimentId: 'idea',
          requestId: 'explicit-opening',
        },
      },
    })
    await waitFor(() =>
      expect(researchDecisions.opened).toHaveBeenCalledWith(
        'idea',
        {
          request_id: 'explicit-opening',
          target: {
            kind: 'direct_report',
            job_id: 'original-study',
            config_id: 'alternative',
            evaluation_id: 'exact-later',
          },
        },
        expect.any(AbortSignal)
      )
    )
    expect(vi.mocked(CandidateDecision).mock.calls.at(-1)![0].preferredEvaluationId).toBe(
      'exact-later'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Selection report' }))
    const params = new URLSearchParams(screen.getByLabelText('Location').textContent!)
    expect(params.get('job')).toBe('prepared-alternative')
    expect(params.get('report')).toBeNull()
    expect(params.get('view')).toBe('backtests')
  })
  it('never acknowledges a newer analysis than the report actually displayed', async () => {
    vi.mocked(researchValidation.context).mockResolvedValue(
      context({
        evaluations: [
          {
            ...later,
            evidence_id: 'new-analysis-pin',
            identity: { ...later.identity, analysis_artifact: 'new-analysis' },
          },
        ],
        action: {
          kind: 'open',
          job_id: later.job_id,
          evidence_id: 'new-analysis-pin',
          view: 'primary',
        },
      })
    )
    mount({
      currentJob: { ...job, id: later.job_id },
      currentResult: {
        ...result,
        report_context: { ...result.report_context!, period: 'evaluation' },
      },
      state: {
        researchValidationOpen: {
          jobId: later.job_id,
          experimentId: 'idea',
          requestId: 'old-action',
          evidenceId: 'exact-later',
        },
      },
    })
    await screen.findByRole('button', { name: 'Selection report' })
    expect(researchDecisions.opened).not.toHaveBeenCalled()
    const decision = vi.mocked(CandidateDecision).mock.calls.at(-1)![0]
    expect(decision.preferredEvaluationId).toBeUndefined()
    expect(decision.target).toMatchObject({
      expected_report: { result_artifact: 'rendered-artifact', analysis_artifact: null },
    })
  })
  it('opens retained embedded later evidence in place without replaying the winner', async () => {
    vi.mocked(researchValidation.context).mockResolvedValue(
      context({
        action: {
          kind: 'open',
          job_id: job.id,
          evidence_id: 'embedded-pin',
          view: 'embedded_later',
        },
      })
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Review later test' }))
    await userEvent.click(screen.getByRole('button', { name: 'Open saved report' }))
    expect(await screen.findByText('Frozen later report')).toBeVisible()
    expect(researchValidation.prepare).not.toHaveBeenCalled()
    expect(researchDecisions.preview).toHaveBeenCalledWith(
      'idea',
      { job_id: 'original-study', config_id: 'alternative' },
      'embedded-pin',
      expect.any(AbortSignal)
    )
    await waitFor(() => expect(researchDecisions.opened).toHaveBeenCalledTimes(1))
  })
})
