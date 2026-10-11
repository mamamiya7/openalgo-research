import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentProps } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type PortfolioJob, portfolioResearch } from '@/api/portfolioResearch'
import type { ResearchFailureReceipt, ResearchRecoveryReceipt } from '@/api/scannerResearch'
import PortfolioResearch from '@/pages/PortfolioResearch'
import { useAuthStore } from '@/stores/authStore'
import { addPortfolioSource, freshPortfolioDraft } from './PortfolioBuilder'

vi.mock('@/api/portfolioResearch', () => ({
  portfolioResearch: {
    job: vi.fn(),
    jobs: vi.fn(),
    capabilities: vi.fn(),
    sources: vi.fn(),
    resume: vi.fn(),
    preflight: vi.fn(),
    submit: vi.fn(),
  },
}))
vi.mock('./PartialStudy', () => ({ PartialStudy: () => null }))
vi.mock('./StudyActivity', () => ({ StudyActivity: () => null }))

const failure: ResearchFailureReceipt = {
  version: 'research-failure-v1',
  code: 'execution_session_hours_mismatch',
  stage: 'prepare_prices',
  message:
    'Saved stock-market context has different execution session hours on 2026-01-22: context 09:15–15:30 IST; execution 09:15–13:00 IST.',
  retryable: false,
  actions: ['new_run', 'edit_setup'],
  details: {
    first_affected_session: '2026-01-22',
    expected_hours: { open: '09:15', close: '15:30' },
    actual_hours: { open: '09:15', close: '13:00' },
    mismatch_count: 2,
  },
}
const recovery: ResearchRecoveryReceipt = {
  version: 'research-recovery-v1',
  status: 'blocked',
  actions: ['new_run', 'edit_setup'],
  message: failure.message,
  checkpoint_phase: 'prepare_prices',
}
const failed: PortfolioJob = {
  id: 'stopped-job',
  kind: 'portfolio_optimize',
  status: 'failed',
  progress: 45,
  created_at: 1,
  error: 'Saved stock-market context has different execution session hours',
  failure,
  recovery,
  resumable: true,
}
const clients: QueryClient[] = []
function Location() {
  return <output data-testid="location">{useLocation().search}</output>
}
function show(
  props: ComponentProps<typeof PortfolioResearch> = {},
  url = '/scanner-research?job=stopped-job'
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <PortfolioResearch {...props} />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
function existingDraft() {
  return addPortfolioSource(
    freshPortfolioDraft(),
    {
      id: 'signals',
      receipt: {
        input_rows: 2,
        signal_count: 2,
        duplicates_removed: 0,
        date_from: '2026-01-22',
        date_to: '2026-02-02',
        symbol_count: 1,
        warnings: [],
      },
      provenance: {
        provider: 'csv',
        exchange: 'NSE',
        interval: 'D',
        adjustment_basis: 'broker',
        calendar_basis: 'native',
        synthetic: false,
      },
      coverage: {},
    },
    'Signals.csv'
  )
}
function display(): NonNullable<PortfolioJob['display']> {
  return {
    version: 'research-result-descriptor-v1',
    role: 'optimization',
    evidence_id: null,
    report_id: null,
    config_id: null,
    calculation_id: null,
    period: null,
    dates: { from: null, to: null, status: 'unknown' },
    input_dates: { from: null, to: null },
    candidate: null,
    parent_job_id: null,
    account: { capital: 100000, currency: 'INR' },
    interval: 'D',
    evaluation_basis_id: null,
    cohort_id: null,
    reservation: null,
    setup: { id: 'saved-version', name: 'Original run rules', number: 3 },
  }
}
beforeEach(() => {
  vi.resetAllMocks()
  sessionStorage.clear()
  localStorage.clear()
  useAuthStore.setState({ user: null })
  vi.mocked(portfolioResearch.job).mockResolvedValue(failed)
  vi.mocked(portfolioResearch.capabilities).mockResolvedValue({
    max_strategies: 8,
    engines: [{ id: 'vectorbt', name: 'VectorBT', available: true, intervals: ['D', '1m'] }],
    optimizers: [{ id: 'optuna', available: true, samplers: ['tpe', 'grid'] }],
  })
})
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
})

describe('structured recovery and action-specific busy state', () => {
  it('explains the first date and actual/expected hours once, suppressing an incompatible Resume flag', async () => {
    const user = userEvent.setup()
    show()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Session hours differ on 22 Jan 2026')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.queryByText(failed.error!)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Resume run' })).not.toBeInTheDocument()
    await user.click(screen.getByText('Run issue details'))
    expect(screen.getByText('Expected · saved context').nextElementSibling).toHaveTextContent(
      '09:15–15:30 IST'
    )
    expect(screen.getByText('Actual · execution calendar').nextElementSibling).toHaveTextContent(
      '09:15–13:00 IST'
    )
    expect(screen.getByText('First affected session').nextElementSibling).toHaveTextContent(
      '22 Jan 2026'
    )
    expect(screen.getByText('Affected sessions').nextElementSibling).toHaveTextContent('2')
    expect(portfolioResearch.resume).not.toHaveBeenCalled()
  })

  it('offers verified daily legacy repair through the existing resume endpoint, preserving the route', async () => {
    const repaired: PortfolioJob = {
      ...failed,
      failure: {
        version: 'research-failure-v1',
        code: 'daily_calendar_hours_omitted',
        stage: 'prepare_prices',
        retryable: true,
        actions: ['resume'],
        message: 'Resume uses the original recorded calendar and completed prices.',
      },
      recovery: {
        ...recovery,
        status: 'available',
        actions: ['resume'],
        message: 'Continue from the saved checkpoint; completed work is retained.',
      },
    }
    vi.mocked(portfolioResearch.job).mockResolvedValue(repaired)
    vi.mocked(portfolioResearch.resume).mockResolvedValue({
      ...repaired,
      status: 'queued',
      failure: null,
      recovery: { ...recovery, status: 'not_needed', actions: [], message: '' },
    })
    show({}, '/scanner-research?job=stopped-job&return_research=experiment%3Dx%26view%3Dstudies')
    await userEvent.click(await screen.findByRole('button', { name: 'Resume run' }))
    expect(portfolioResearch.resume).toHaveBeenCalledWith('stopped-job', expect.any(AbortSignal))
    expect(screen.getByTestId('location')).toHaveTextContent('return_research=')
    expect(portfolioResearch.submit).not.toHaveBeenCalled()
  })

  it('restores only the explicitly linked setup through the workspace recovery callback, without completed-result reuse', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({ ...failed, display: display() })
    const recover = vi.fn().mockResolvedValue(undefined)
    const reuse = vi.fn()
    const change = vi.fn()
    show({
      onUseSetup: reuse,
      workspace: {
        experimentId: 'experiment',
        draft: existingDraft(),
        onChange: change,
        onRun: vi.fn(),
        onOpenJob: vi.fn(),
        onRecoverSetup: recover,
      },
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Prepare new run' }))
    expect(recover).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'stopped-job',
        display: expect.objectContaining({
          setup: expect.objectContaining({ id: 'saved-version' }),
        }),
      }),
      expect.any(AbortSignal)
    )
    expect(reuse).not.toHaveBeenCalled()
    expect(change).not.toHaveBeenCalled()
    expect(portfolioResearch.preflight).not.toHaveBeenCalled()
  })

  it('opens current setup transparently when no linked version exists, leaving its signals and rules intact', async () => {
    const current = existingDraft()
    sessionStorage.setItem('portfolio-draft:account', JSON.stringify(current))
    const reuse = vi.fn()
    show({ onUseSetup: reuse })
    expect(await screen.findByText(/This run has no linked setup version/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Open current setup' }))
    expect(await screen.findByRole('button', { name: 'Run backtest' })).toBeVisible()
    expect(screen.getByTestId('location')).not.toHaveTextContent('job=')
    const retained = JSON.parse(sessionStorage.getItem('portfolio-draft:account')!)
    expect(retained.portfolio).toEqual(current.portfolio)
    expect(reuse).not.toHaveBeenCalled()
  })

  it('aborts a pending setup recovery when the view unmounts and ignores its late failure', async () => {
    vi.mocked(portfolioResearch.job).mockResolvedValue({ ...failed, display: display() })
    let reject!: (reason: Error) => void
    const recover = vi.fn(
      (_job: PortfolioJob, _signal: AbortSignal) =>
        new Promise<void>((_, fail) => {
          reject = fail
        })
    )
    const view = show({
      workspace: {
        draft: existingDraft(),
        onChange: vi.fn(),
        onRun: vi.fn(),
        onOpenJob: vi.fn(),
        onRecoverSetup: recover,
      },
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Prepare new run' }))
    const signal = recover.mock.calls[0][1] as AbortSignal
    view.unmount()
    expect(signal.aborted).toBe(true)
    await act(async () => reject(new Error('Late restore failure')))
  })

  it('keeps Save/setup locks separate from an actual run submission label', async () => {
    show(
      {
        workspace: {
          draft: existingDraft(),
          onChange: vi.fn(),
          onRun: vi.fn(),
          onOpenJob: vi.fn(),
          disabled: true,
        },
      },
      '/scanner-research'
    )
    expect(await screen.findByRole('button', { name: 'Run backtest' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Starting…' })).not.toBeInTheDocument()
  })

  it('shows Starting only while a calculation submission is actually pending', async () => {
    let complete!: (job: PortfolioJob) => void
    const run = vi.fn(
      () =>
        new Promise<PortfolioJob>((resolve) => {
          complete = resolve
        })
    )
    const open = vi.fn()
    show(
      { workspace: { draft: existingDraft(), onChange: vi.fn(), onRun: run, onOpenJob: open } },
      '/scanner-research'
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Run backtest' }))
    expect(screen.getByRole('button', { name: 'Starting…' })).toBeDisabled()
    await act(async () => complete({ ...failed, status: 'queued', failure: null }))
    await waitFor(() => expect(open).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeEnabled()
  })
})
