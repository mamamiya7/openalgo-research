import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PortfolioJob, PortfolioResult } from '@/api/portfolioResearch'
import { type BaselineMatch, researchBaselines } from '@/api/researchBaselines'
import { researchComparisons, type SavedComparison } from '@/api/researchComparisons'
import {
  researchShortlist,
  type ShortlistCandidate,
  type ShortlistDetail,
} from '@/api/researchShortlist'
import { CompareMatchedBaseline, MatchBaseline } from './MatchedBaseline'

vi.mock('@/api/researchBaselines', async (original) => ({
  ...(await original<typeof import('@/api/researchBaselines')>()),
  researchBaselines: { context: vi.fn(), prepare: vi.fn() },
}))
vi.mock('@/api/researchComparisons', async (original) => ({
  ...(await original<typeof import('@/api/researchComparisons')>()),
  researchComparisons: { save: vi.fn() },
}))
vi.mock('@/api/researchShortlist', async (original) => ({
  ...(await original<typeof import('@/api/researchShortlist')>()),
  researchShortlist: { get: vi.fn(), save: vi.fn() },
}))
const study = {
  id: 'study-candidate',
  name: 'Alternative trial',
  source_job_id: 'study',
  source_result_artifact: 'study-evidence',
} as ShortlistCandidate
const baseline = {
  id: 'baseline-candidate',
  name: 'Original baseline',
  source_job_id: 'baseline',
  source_result_artifact: 'baseline-evidence',
} as ShortlistCandidate
const context: BaselineMatch = {
  version: '1',
  revision: 4,
  archived: false,
  study: { job_id: 'study', result_artifact: 'study-evidence' },
  baseline: { job_id: 'baseline', result_artifact: 'baseline-evidence' },
  period: 'selection',
  dates: { from: '2026-01-21', to: '2026-06-30' },
  recipe: {
    config_id: 'baseline-settings',
    evaluation_basis_id: 'same-cohort',
    eligible_signals: 64,
    excluded_signals: 4,
  },
  differences: ['cohort', 'period'],
  action: { kind: 'prepare' },
}
const job: PortfolioJob = {
  id: 'matched-result',
  status: 'completed',
  progress: 100,
  created_at: 1,
}
const result = {
  report_context: {
    result_artifact: 'matched-evidence',
    config_id: 'baseline-settings',
    period: 'selection',
  },
  matched_baseline_origin: {
    version: '1',
    study_job_id: 'study',
    study_result_artifact: 'study-evidence',
    baseline_job_id: 'baseline',
    baseline_result_artifact: 'baseline-evidence',
    verification: { settings: 'matched', basis: 'matched' },
  },
} as PortfolioResult
const clients: QueryClient[] = []
function Location() {
  return <output data-testid="location">{useLocation().search}</output>
}
function mount(complete = false, readOnly = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          '/?experiment=e&view=shortlist&compare_candidates=study-candidate,baseline-candidate&matched_study_candidate=study-candidate&matched_compare_request=stable-comparison',
        ]}
      >
        {complete ? (
          <CompareMatchedBaseline
            owner="owner"
            experimentId="e"
            job={job}
            result={result}
            readOnly={readOnly}
          />
        ) : (
          <MatchBaseline
            owner="owner"
            experimentId="e"
            study={study}
            baseline={baseline}
            readOnly={readOnly}
          />
        )}
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(researchBaselines.context).mockResolvedValue(context)
  vi.mocked(researchBaselines.prepare).mockResolvedValue({
    job: { ...job, status: 'queued' },
    reused: false,
    context,
  })
  vi.mocked(researchShortlist.get).mockResolvedValue({
    candidate: study,
    available: true,
    archived: false,
  } as ShortlistDetail)
  vi.mocked(researchShortlist.save).mockResolvedValue({
    candidate: {
      ...baseline,
      id: 'matched-candidate',
      source_job_id: job.id,
      source_result_artifact: 'matched-evidence',
      config_id: 'baseline-settings',
      period: 'selection',
    },
    reused: false,
  })
  vi.mocked(researchComparisons.save).mockResolvedValue({
    comparison: { id: 'matched-comparison' } as SavedComparison,
    reused: false,
  })
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
})
describe('matching a baseline for fair comparison', () => {
  it('does no work until review, then preserves both source artifacts and the selected alternative', async () => {
    mount()
    expect(researchBaselines.context).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Match baseline' }))
    expect(await screen.findByText('64 eligible signals')).toBeVisible()
    expect(screen.getByText('Align: Eligible signals · Evaluation period')).toBeVisible()
    expect(researchBaselines.prepare).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Create matching baseline' }))
    expect(researchBaselines.prepare).toHaveBeenCalledWith(
      'e',
      'study',
      'baseline',
      {
        revision: 4,
        request_id: expect.any(String),
        study_result_artifact: 'study-evidence',
        baseline_result_artifact: 'baseline-evidence',
      },
      expect.any(AbortSignal)
    )
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('job=matched-result')
    )
    expect(screen.getByTestId('location')).toHaveTextContent(
      'matched_study_candidate=study-candidate'
    )
  })
  it('rejects a changed source instead of matching a newer report silently', async () => {
    vi.mocked(researchBaselines.context).mockResolvedValue({
      ...context,
      study: { ...context.study, result_artifact: 'replacement' },
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Match baseline' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('changed')
    expect(screen.getByRole('button', { name: 'Create matching baseline' })).toBeDisabled()
    expect(researchBaselines.prepare).not.toHaveBeenCalled()
  })
  it('shows an incompatible recipe without admitting a run', async () => {
    vi.mocked(researchBaselines.context).mockResolvedValue({
      ...context,
      recipe: null,
      action: { kind: 'unavailable', reason: 'Study and baseline must use the same costs' },
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Match baseline' }))
    expect(await screen.findByText('Study and baseline must use the same costs')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Create matching baseline' })
    ).not.toBeInTheDocument()
  })
  it('reopens an existing match without calculating again', async () => {
    vi.mocked(researchBaselines.context).mockResolvedValue({
      ...context,
      action: { kind: 'open', job_id: job.id },
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Match baseline' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Open matching baseline' }))
    expect(screen.getByTestId('location')).toHaveTextContent('job=matched-result')
    expect(researchBaselines.prepare).not.toHaveBeenCalled()
  })
  it('retries the same admission and aborts when review closes', async () => {
    vi.mocked(researchBaselines.prepare).mockRejectedValueOnce(new Error('temporary'))
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Match baseline' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Create matching baseline' }))
    await screen.findByRole('alert')
    vi.mocked(researchBaselines.prepare).mockImplementationOnce(() => new Promise(() => {}))
    await userEvent.click(screen.getByRole('button', { name: 'Create matching baseline' }))
    const calls = vi.mocked(researchBaselines.prepare).mock.calls
    expect(calls[0][3]).toEqual(calls[1][3])
    await userEvent.keyboard('{Escape}')
    expect(calls[1][4]?.aborted).toBe(true)
  })
  it('creates a normal comparison with the new baseline as reference and retains retries', async () => {
    vi.mocked(researchComparisons.save).mockRejectedValueOnce(new Error('temporary'))
    mount(true)
    expect(researchShortlist.save).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Compare with study' }))
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'Compare with study' }))
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('comparison=matched-comparison')
    )
    expect(researchShortlist.save).toHaveBeenCalledWith(
      'e',
      {
        job_id: 'matched-result',
        config_id: 'baseline-settings',
        expected_result_artifact: 'matched-evidence',
      },
      expect.any(AbortSignal)
    )
    expect(researchComparisons.save).toHaveBeenLastCalledWith(
      'e',
      {
        candidate_ids: ['study-candidate', 'matched-candidate'],
        reference_candidate_id: 'matched-candidate',
        request_id: 'stable-comparison',
      },
      expect.any(AbortSignal)
    )
    expect(researchBaselines.prepare).not.toHaveBeenCalled()
  })
  it('never substitutes another study when the original bookmark changed', async () => {
    vi.mocked(researchShortlist.get).mockResolvedValue({
      candidate: { ...study, source_result_artifact: 'other-study' },
      available: true,
      archived: false,
    } as ShortlistDetail)
    mount(true)
    await userEvent.click(screen.getByRole('button', { name: 'Compare with study' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('study candidate changed')
    expect(researchShortlist.save).not.toHaveBeenCalled()
    expect(researchComparisons.save).not.toHaveBeenCalled()
  })
  it('refuses a baseline artifact different from the displayed result before comparison', async () => {
    vi.mocked(researchShortlist.save).mockResolvedValue({
      candidate: {
        ...baseline,
        id: 'matched-candidate',
        source_job_id: job.id,
        source_result_artifact: 'replacement',
        config_id: 'baseline-settings',
        period: 'selection',
      },
      reused: true,
    })
    mount(true)
    await userEvent.click(screen.getByRole('button', { name: 'Compare with study' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('matching baseline changed')
    expect(researchComparisons.save).not.toHaveBeenCalled()
  })
  it('keeps archived results readable without creating a match or comparison', () => {
    mount(true, true)
    expect(screen.queryByRole('button', { name: 'Compare with study' })).not.toBeInTheDocument()
    expect(researchComparisons.save).not.toHaveBeenCalled()
  })
})
