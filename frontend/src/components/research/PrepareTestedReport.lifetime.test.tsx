import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import {
  type CandidateReportReceipt,
  type CandidateReportsReceipt,
  researchCandidates,
} from '@/api/researchCandidates'
import { PrepareTestedReport } from './PrepareTestedReport'

vi.mock('@/api/researchCandidates', () => ({
  researchCandidates: { get: vi.fn(), prepare: vi.fn() },
}))

let client: QueryClient
afterEach(() => {
  cleanup()
  client?.clear()
  vi.resetAllMocks()
})

it('ignores a prepared report from a study and configuration that are no longer being viewed', async () => {
  const receipt = (jobId: string): CandidateReportsReceipt => ({
    version: 'research-candidate-reports-v1',
    study_job_id: jobId,
    period: 'selection',
    candidates: [
      {
        config_id: jobId === 'study' ? 'original' : 'another-config',
        trial_number: null,
        is_objective_winner: false,
        is_selected: true,
        status: 'available',
      },
    ],
  })
  vi.mocked(researchCandidates.get).mockImplementation(async (jobId) => receipt(jobId))
  let finish!: (value: CandidateReportReceipt) => void
  const pending = new Promise<CandidateReportReceipt>((resolve) => {
    finish = resolve
  })
  vi.mocked(researchCandidates.prepare).mockReturnValue(pending)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const open = vi.fn()
  const content = (jobId: string, configId: string) => (
    <QueryClientProvider client={client}>
      <PrepareTestedReport jobId={jobId} configId={configId} readOnly={false} onOpen={open} />
    </QueryClientProvider>
  )
  const view = render(content('study', 'original'))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Prepare tested report' })).toBeEnabled()
  )
  await userEvent.click(screen.getByRole('button', { name: 'Prepare tested report' }))
  const signal = vi.mocked(researchCandidates.prepare).mock.calls[0][2]!

  view.rerender(content('another-study', 'another-config'))
  expect(signal.aborted).toBe(true)
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Prepare tested report' })).toBeEnabled()
  )
  const update = vi.spyOn(client, 'setQueryData')
  await act(async () => {
    finish({
      ...receipt('study').candidates[0],
      status: 'ready',
      report_job_id: 'old-exact-report',
    })
    await pending
  })
  expect(update).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: 'Open tested report' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Prepare tested report' })).toBeEnabled()
  expect(open).not.toHaveBeenCalled()
  expect(researchCandidates.prepare).toHaveBeenCalledOnce()
})
