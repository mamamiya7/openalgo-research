import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { researchChosenSetups } from '@/api/researchChosenSetups'
import {
  type DecisionContext,
  type DecisionEvent,
  researchDecisions,
} from '@/api/researchDecisions'
import { SaveTestedSetup } from './SaveTestedSetup'

vi.mock('@/api/researchDecisions', async (original) => ({
  ...(await original<typeof import('@/api/researchDecisions')>()),
  researchDecisions: { context: vi.fn(), save: vi.fn() },
}))
vi.mock('@/api/researchChosenSetups', async (original) => ({
  ...(await original<typeof import('@/api/researchChosenSetups')>()),
  researchChosenSetups: { context: vi.fn(), choose: vi.fn() },
}))
const event = { id: 'event', decision_id: 'decision', state: 'keep', revision: 1 } as DecisionEvent
const target = {
  job_id: 'job',
  config_id: 'config',
  expected_report: { result_artifact: 'result', analysis_artifact: null },
}
let client: QueryClient
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(researchDecisions.context).mockResolvedValue({
    current: null,
    archived: false,
  } as DecisionContext)
  vi.mocked(researchDecisions.save).mockResolvedValue({
    event,
    decision: { id: 'decision', revision: 1, current: event, archived: false },
    reused: false,
  })
  vi.mocked(researchChosenSetups.context).mockResolvedValue({
    revision: 7,
    archived: false,
    current: null,
    eligibility: { available: true },
  } as Awaited<ReturnType<typeof researchChosenSetups.context>>)
  vi.mocked(researchChosenSetups.choose).mockResolvedValue({
    revision: 8,
    reused: false,
  } as Awaited<ReturnType<typeof researchChosenSetups.choose>>)
})
afterEach(() => {
  cleanup()
  client?.clear()
})
function mount(readOnly = false) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SaveTestedSetup
          experimentId="idea"
          target={target}
          name="Scanner · tested"
          readOnly={readOnly}
        />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
it('saves exact evidence and an editable name through fenced Keep then Choose', async () => {
  mount()
  expect(researchDecisions.context).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Save tested setup' }))
  const name = await screen.findByLabelText('Setup name')
  await userEvent.clear(name)
  await userEvent.type(name, 'My tested rules')
  await userEvent.type(screen.getByLabelText('Note (optional)'), 'Research only')
  await userEvent.click(screen.getByRole('button', { name: 'Save setup', exact: true }))
  await waitFor(() => expect(researchChosenSetups.choose).toHaveBeenCalledOnce())
  expect(researchDecisions.save).toHaveBeenCalledWith(
    'idea',
    target,
    expect.objectContaining({
      state: 'keep',
      revision: 0,
      reason: 'Research only',
      evaluation_id: null,
      request_id: expect.any(String),
    }),
    expect.any(AbortSignal)
  )
  expect(researchChosenSetups.choose).toHaveBeenCalledWith(
    'idea',
    expect.objectContaining({
      decision_id: 'decision',
      event_id: 'event',
      revision: 7,
      name: 'My tested rules',
    }),
    expect.any(AbortSignal)
  )
})
it('retries an unknown Choose outcome with the same request without duplicating Keep', async () => {
  vi.mocked(researchChosenSetups.choose).mockRejectedValueOnce(new Error('Connection lost'))
  mount()
  await userEvent.click(screen.getByRole('button', { name: 'Save tested setup' }))
  await screen.findByLabelText('Setup name')
  await userEvent.click(screen.getByRole('button', { name: 'Save setup', exact: true }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Your Keep decision is saved. Connection lost'
  )
  expect(screen.getByLabelText('Setup name')).toHaveAttribute('readonly')
  await userEvent.click(screen.getByRole('button', { name: 'Finish saving setup' }))
  await waitFor(() => expect(researchChosenSetups.choose).toHaveBeenCalledTimes(2))
  expect(vi.mocked(researchChosenSetups.choose).mock.calls[0][1]).toEqual(
    vi.mocked(researchChosenSetups.choose).mock.calls[1][1]
  )
  expect(researchDecisions.save).toHaveBeenCalledOnce()
})
it('reuses an existing Keep event and blocks an ineligible exact setup', async () => {
  vi.mocked(researchDecisions.context).mockResolvedValue({
    current: { current: event },
    archived: false,
  } as DecisionContext)
  vi.mocked(researchChosenSetups.context).mockResolvedValue({
    revision: 7,
    archived: false,
    current: null,
    eligibility: { available: false, reason: 'The frozen prices are unavailable.' },
  } as Awaited<ReturnType<typeof researchChosenSetups.context>>)
  mount()
  await userEvent.click(screen.getByRole('button', { name: 'Save tested setup' }))
  await screen.findByLabelText('Setup name')
  await userEvent.click(screen.getByRole('button', { name: 'Save setup', exact: true }))
  expect(await screen.findByRole('alert')).toHaveTextContent('frozen prices are unavailable')
  expect(screen.getByRole('button', { name: 'Review saved evidence' })).toBeVisible()
  expect(researchDecisions.save).not.toHaveBeenCalled()
  expect(researchChosenSetups.choose).not.toHaveBeenCalled()
})
it('does not expose a write action in read-only studies', () => {
  mount(true)
  expect(screen.getByRole('button', { name: 'Save tested setup' })).toBeDisabled()
  expect(researchDecisions.context).not.toHaveBeenCalled()
})

it('aborts a pending Keep when the viewed evidence changes and never chooses its late event', async () => {
  let finish!: (value: Awaited<ReturnType<typeof researchDecisions.save>>) => void
  vi.mocked(researchDecisions.save).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  const view = mount()
  await userEvent.click(screen.getByRole('button', { name: 'Save tested setup' }))
  await screen.findByLabelText('Setup name')
  await userEvent.click(screen.getByRole('button', { name: 'Save setup', exact: true }))
  await waitFor(() => expect(researchDecisions.save).toHaveBeenCalledOnce())
  const signal = vi.mocked(researchDecisions.save).mock.calls[0][3]!
  view.rerender(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SaveTestedSetup
          experimentId="idea"
          target={{ ...target, config_id: 'different-config' }}
          name="Other tested rules"
          readOnly={false}
        />
      </MemoryRouter>
    </QueryClientProvider>
  )
  expect(signal.aborted).toBe(true)
  finish({
    event,
    decision: { id: 'decision', revision: 1, current: event, archived: false },
    reused: false,
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(researchChosenSetups.context).not.toHaveBeenCalled()
  expect(researchChosenSetups.choose).not.toHaveBeenCalled()
})
