import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { type CandidateReportsReceipt, researchCandidates } from '@/api/researchCandidates'
import { PrepareTestedReport } from './PrepareTestedReport'

vi.mock('@/api/researchCandidates', () => ({
  researchCandidates: { get: vi.fn(), prepare: vi.fn() },
}))
let client: QueryClient
const open = vi.fn()
const receipt = (status: 'available' | 'queued' | 'ready'): CandidateReportsReceipt => ({
  version: 'research-candidate-reports-v1',
  study_job_id: 'study',
  period: 'selection',
  candidates: [
    {
      config_id: 'original',
      trial_number: null,
      is_objective_winner: false,
      is_selected: true,
      status,
      ...(status !== 'available' ? { report_job_id: 'exact' } : {}),
    },
  ],
})
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(researchCandidates.get).mockResolvedValue(receipt('available'))
})
afterEach(() => {
  cleanup()
  client?.clear()
})
function mount(readOnly = false) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <PrepareTestedReport jobId="study" configId="original" readOnly={readOnly} onOpen={open} />
    </QueryClientProvider>
  )
}
it('requires an explicit one-time preparation, then opens the retained report', async () => {
  mount()
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Prepare tested report' })).toBeEnabled()
  )
  expect(researchCandidates.prepare).not.toHaveBeenCalled()
  vi.mocked(researchCandidates.prepare).mockResolvedValue(receipt('queued').candidates[0])
  vi.mocked(researchCandidates.get).mockResolvedValue(receipt('ready'))
  await userEvent.click(screen.getByRole('button', { name: 'Prepare tested report' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Open tested report' }))
  expect(researchCandidates.prepare).toHaveBeenCalledExactlyOnceWith(
    'study',
    'original',
    expect.any(AbortSignal)
  )
  expect(open).toHaveBeenCalledWith('exact')
})
it('opens an already saved report without another calculation', async () => {
  vi.mocked(researchCandidates.get).mockResolvedValue(receipt('ready'))
  mount(true)
  await userEvent.click(await screen.findByRole('button', { name: 'Open tested report' }))
  expect(researchCandidates.prepare).not.toHaveBeenCalled()
})
it('prevents archived or read-only report preparation', async () => {
  mount(true)
  expect(await screen.findByRole('button', { name: 'Prepare tested report' })).toBeDisabled()
  expect(researchCandidates.prepare).not.toHaveBeenCalled()
})
