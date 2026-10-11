import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useSearchParams } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
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

let client: QueryClient
afterEach(() => {
  cleanup()
  client?.clear()
  vi.restoreAllMocks()
  vi.resetAllMocks()
})

function Workspace() {
  const [params, setParams] = useSearchParams()
  return (
    <>
      <output aria-label="Current research location">{params.toString()}</output>
      <button
        type="button"
        onClick={() => setParams({ experiment: 'another-idea', view: 'setup' })}
      >
        Open another experiment
      </button>
      <SaveTestedSetup
        experimentId="idea"
        target={{
          job_id: 'job',
          config_id: 'config',
          expected_report: { result_artifact: 'result', analysis_artifact: null },
        }}
        name="Exact tested rules"
        readOnly={false}
      />
    </>
  )
}

it('does not steal navigation after a committed save is closed during query refresh', async () => {
  const event = {
    id: 'event',
    decision_id: 'decision',
    state: 'keep',
    revision: 1,
  } as DecisionEvent
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

  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  let finishRefresh!: () => void
  const refreshing = new Promise<void>((resolve) => {
    finishRefresh = resolve
  })
  const invalidate = vi.spyOn(client, 'invalidateQueries').mockReturnValue(refreshing)
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/?experiment=idea&view=results']}>
        <Workspace />
      </MemoryRouter>
    </QueryClientProvider>
  )
  await userEvent.click(screen.getByRole('button', { name: 'Save tested setup' }))
  await screen.findByLabelText('Setup name')
  await userEvent.click(screen.getByRole('button', { name: 'Save setup', exact: true }))
  await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(3))
  const signal = vi.mocked(researchChosenSetups.choose).mock.calls[0][2]!

  await userEvent.keyboard('{Escape}')
  expect(signal.aborted).toBe(true)
  await userEvent.click(screen.getByRole('button', { name: 'Open another experiment' }))
  expect(screen.getByLabelText('Current research location')).toHaveTextContent(
    'experiment=another-idea&view=setup'
  )

  await act(async () => {
    finishRefresh()
    await refreshing
  })
  expect(screen.getByLabelText('Current research location')).toHaveTextContent(
    'experiment=another-idea&view=setup'
  )
  expect(researchDecisions.save).toHaveBeenCalledOnce()
  expect(researchChosenSetups.choose).toHaveBeenCalledOnce()
})
