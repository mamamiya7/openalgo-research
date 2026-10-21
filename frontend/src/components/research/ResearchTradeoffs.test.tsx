import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { type ResearchTradeoffsView, researchTradeoffs } from '@/api/researchTradeoffs'
import { useAuthStore } from '@/stores/authStore'
import { ResearchTradeoffs } from './ResearchTradeoffs'

vi.mock('@/api/researchTradeoffs', () => ({ researchTradeoffs: { get: vi.fn() } }))
const pointer = 'a'.repeat(64)
const choice = 'b'.repeat(64)
const clients: QueryClient[] = []
function fixture(): ResearchTradeoffsView {
  return {
    version: 'research-tradeoffs-view-v1',
    job_id: 'saved-study',
    result_artifact: pointer,
    available: true,
    reason: null,
    analysis: {
      version: 'research-development-tradeoffs-v1',
      status: 'available',
      counts: { recorded: 12, eligible: 8, frontier: 3, excluded: 4 },
      baseline: {
        config_id: 'c'.repeat(64),
        metrics: { net_return_pct: 1, max_drawdown_pct: 3, closed_trades: 20 },
        selected: false,
      },
      choices: [
        {
          config_id: choice,
          roles: ['best_return'],
          metrics: { net_return_pct: 5, max_drawdown_pct: 2, closed_trades: 10 },
          selected_existing: false,
          validation: 'exploratory_development_only',
        },
      ],
      limitations: [],
    },
  }
}
function mount(onInspect = vi.fn(), resultArtifact: string | undefined = pointer) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  return {
    ...render(
      <QueryClientProvider client={client}>
        <ResearchTradeoffs
          jobId="saved-study"
          resultArtifact={resultArtifact}
          onInspect={onInspect}
        />
      </QueryClientProvider>
    ),
    onInspect,
  }
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { username: 'first' } as never })
  vi.mocked(researchTradeoffs.get).mockResolvedValue(fixture())
})
afterEach(() => {
  cleanup()
  clients.forEach((client) => client.clear())
  clients.length = 0
})

it('loads only on request and opens the exact saved settings without changing the recommendation', async () => {
  const { onInspect, container } = mount()
  expect(researchTradeoffs.get).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Compare alternatives' }))
  const table = await screen.findByRole('table', { name: 'Saved development trade-offs' })
  expect(researchTradeoffs.get).toHaveBeenCalledWith(
    'saved-study',
    pointer,
    expect.any(AbortSignal)
  )
  expect(within(table).getByText('Unchanged reference')).toBeInTheDocument()
  expect(within(table).getByText('5%')).toBeInTheDocument()
  expect(screen.getByText(/recommendation remains unchanged/)).toBeInTheDocument()
  expect(
    screen.getByText(/Trade counts are not turnover, liquidity or confidence/)
  ).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Inspect Higher return settings' }))
  expect(onInspect).toHaveBeenCalledExactlyOnceWith(choice)
  expect((await axe(container)).violations).toEqual([])
})

it('does not request an unanchored saved result', () => {
  mount(vi.fn(), 'unverified')
  expect(screen.queryByRole('button', { name: 'Compare alternatives' })).not.toBeInTheDocument()
  expect(researchTradeoffs.get).not.toHaveBeenCalled()
})

it('reports unavailable comparisons without inventing rows', async () => {
  vi.mocked(researchTradeoffs.get).mockResolvedValue({
    ...fixture(),
    available: false,
    analysis: null,
    reason: 'No comparable saved accounts.',
  })
  mount()
  await userEvent.click(screen.getByRole('button', { name: 'Compare alternatives' }))
  expect(await screen.findByText('No comparable saved accounts.')).toBeInTheDocument()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})

it('shows the reference when it beats every eligible alternative', async () => {
  const data = fixture()
  data.analysis!.status = 'no_eligible_alternatives'
  data.analysis!.choices = []
  data.analysis!.counts.frontier = 0
  vi.mocked(researchTradeoffs.get).mockResolvedValue(data)
  mount()
  await userEvent.click(screen.getByRole('button', { name: 'Compare alternatives' }))
  expect(await screen.findByText('Unchanged reference')).toBeInTheDocument()
  expect(
    screen.getByText(/No eligible alternative improves on the unchanged reference/)
  ).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Inspect .* settings/ })).not.toBeInTheDocument()
})

it('keeps qualified alternatives usable when reference metrics cannot be verified', async () => {
  const data = fixture()
  data.analysis!.baseline!.metrics = null
  vi.mocked(researchTradeoffs.get).mockResolvedValue(data)
  mount()
  await userEvent.click(screen.getByRole('button', { name: 'Compare alternatives' }))
  expect(
    await screen.findByRole('button', { name: 'Inspect Higher return settings' })
  ).toBeEnabled()
  expect(screen.queryByText('Unchanged reference')).not.toBeInTheDocument()
})

it('rejects a changed result instead of opening stale settings', async () => {
  vi.mocked(researchTradeoffs.get).mockResolvedValue({
    ...fixture(),
    result_artifact: 'd'.repeat(64),
  })
  const { onInspect } = mount()
  await userEvent.click(screen.getByRole('button', { name: 'Compare alternatives' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Saved results changed')
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(onInspect).not.toHaveBeenCalled()
})

it('keeps a changed-result conflict actionable without retrying the obsolete pointer', async () => {
  vi.mocked(researchTradeoffs.get).mockRejectedValue({
    isAxiosError: true,
    response: { status: 409 },
  })
  mount()
  await userEvent.click(screen.getByRole('button', { name: 'Compare alternatives' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Reopen this study')
  expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
})

it('clears the open comparison when the account changes', async () => {
  mount()
  await userEvent.click(screen.getByRole('button', { name: 'Compare alternatives' }))
  await screen.findByRole('table')
  act(() => useAuthStore.setState({ user: { username: 'second' } as never }))
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Compare alternatives' })).toHaveAttribute(
    'aria-expanded',
    'false'
  )
  expect(researchTradeoffs.get).toHaveBeenCalledTimes(1)
})

it('cancels an outstanding read when the comparison closes', async () => {
  let signal: AbortSignal | undefined
  vi.mocked(researchTradeoffs.get).mockImplementation((_job, _pointer, current) => {
    signal = current
    return new Promise(() => {})
  })
  mount()
  await userEvent.click(screen.getByRole('button', { name: 'Compare alternatives' }))
  await waitFor(() => expect(signal).toBeDefined())
  await userEvent.click(screen.getByRole('button', { name: 'Hide alternatives' }))
  expect(signal?.aborted).toBe(true)
})
