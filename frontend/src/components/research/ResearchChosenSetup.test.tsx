import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { portfolioResearch } from '@/api/portfolioResearch'
import {
  type ChosenSetup,
  type ChosenSetupContext,
  type ReuseSetupPreview,
  researchChosenSetups,
} from '@/api/researchChosenSetups'
import type { DecisionEvent } from '@/api/researchDecisions'
import { useResearchWorkspaceMutations } from '@/hooks/useResearchWorkspaceMutations'
import { useAuthStore } from '@/stores/authStore'
import { freshPortfolioDraft } from './PortfolioBuilder'
import { ChooseResearchSetup, ResearchChosenSetup } from './ResearchChosenSetup'
import { ReuseChosenSetup } from './ReuseChosenSetup'

vi.mock('@/api/researchChosenSetups', async (original) => ({
  ...(await original<typeof import('@/api/researchChosenSetups')>()),
  researchChosenSetups: {
    context: vi.fn(),
    choose: vi.fn(),
    use: vi.fn(),
    history: vi.fn(),
    preview: vi.fn(),
    replay: vi.fn(),
  },
}))
vi.mock('@/api/portfolioResearch', () => ({
  portfolioResearch: { sources: vi.fn(), upload: vi.fn() },
}))
vi.mock('@/hooks/useResearchWorkspaceMutations', () => ({ useResearchWorkspaceMutations: vi.fn() }))
const beforeChange = vi.fn(),
  afterChoice = vi.fn()
const strategy = {
  id: 'scanner',
  name: 'Momentum',
  source_id: 'old-signals',
  type: 'signals' as const,
  allocation_pct: 100,
  search: {},
  config: { target_pct: 4, stop_pct: 2, hold_sessions: 3, order_size_pct: 25, cost_bps: 5 },
}
const source = {
  id: 'old-signals',
  receipt: {
    filename: 'old.csv',
    input_rows: 10,
    signal_count: 10,
    duplicates_removed: 0,
    date_from: '2026-01-05',
    date_to: '2026-07-10',
    symbol_count: 3,
    warnings: [],
  },
}
const event: DecisionEvent = {
  id: 'keep-event',
  decision_id: 'kept',
  revision: 1,
  supersedes_event_id: null,
  state: 'keep',
  reason: 'Promising',
  created_at: 1,
  candidate_name: 'Trial 16',
  comparison_name: null,
  comparison_id: null,
  member_id: null,
  source_job_id: 'study',
  source_result_artifact: 'a'.repeat(64),
  config_id: 'trial16',
  period: 'selection',
  trial_number: 15,
  proposal_number: null,
  is_objective_winner: false,
  evaluation: null,
  evidence_use: {
    reservation: null,
    reservation_status: 'unknown',
    calculation: 'unknown',
    opened_at: null,
    later_used_for_decision: false,
    coverage: 'legacy_unknown',
    overlap: 'not_checked',
  },
}
const choice: ChosenSetup = {
  id: 'choice',
  sequence: 1,
  name: 'Momentum chosen',
  version: { id: 'frozen', name: 'Momentum chosen', number: 4 },
  decision_id: 'kept',
  event_id: 'keep-event',
  source_job_id: 'study',
  source_result_artifact: 'a'.repeat(64),
  config_id: 'trial16',
  period: 'selection',
  created_at: 1,
  current_decision_state: 'keep',
  usable: true,
  strategies: [strategy],
  capital: 100000,
  dates: { from: '2026-01-05', to: '2026-07-10' },
}
const context: ChosenSetupContext = {
  revision: 7,
  archived: false,
  current: choice,
  eligibility: {
    available: true,
    candidate_name: 'Trial 16',
    decision_id: 'kept',
    event_id: 'keep-event',
    strategies: [strategy],
    capital: 100000,
    period: 'selection',
    dates: choice.dates,
  },
}
function preview(): ReuseSetupPreview {
  const draft = freshPortfolioDraft()
  draft.portfolio.strategies = [structuredClone(strategy)]
  draft.sources = { 'old-signals': structuredClone(source) }
  return {
    revision: 7,
    archived: false,
    choice: structuredClone(choice),
    draft,
    changes: { sources: [], fields: [], rules_unchanged: true },
  }
}
function Location() {
  const location = useLocation()
  return <output aria-label="Location">{location.search}</output>
}
function mount(component: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const rendered = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/scanner-research?experiment=idea&view=overview']}>
        {component}
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return { ...rendered, client }
}
beforeEach(() => {
  vi.clearAllMocks()
  beforeChange.mockReset().mockResolvedValue(undefined)
  afterChoice.mockReset().mockResolvedValue(undefined)
  vi.mocked(useResearchWorkspaceMutations).mockReturnValue({ beforeChange, afterChoice })
  useAuthStore.setState({ user: { username: 'alice', broker: 'fixture' } })
  vi.mocked(researchChosenSetups.context).mockResolvedValue(structuredClone(context))
  vi.mocked(researchChosenSetups.choose).mockResolvedValue({ choice, revision: 8, reused: false })
  vi.mocked(researchChosenSetups.preview).mockResolvedValue(preview())
  vi.mocked(portfolioResearch.sources).mockResolvedValue({ items: [source], next_offset: null })
})
afterEach(cleanup)

it('chooses the saved Keep event deliberately without applying rules or starting a run', async () => {
  const user = userEvent.setup()
  mount(<ChooseResearchSetup experimentId="idea" event={event} readOnly={false} />)
  expect(researchChosenSetups.context).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Choose setup' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByText(/TP 4%/)
  await user.clear(within(dialog).getByLabelText('Setup name'))
  await user.type(within(dialog).getByLabelText('Setup name'), 'My retained momentum')
  await user.click(within(dialog).getByRole('button', { name: 'Choose setup' }))
  await waitFor(() =>
    expect(researchChosenSetups.choose).toHaveBeenCalledWith(
      'idea',
      {
        revision: 7,
        request_id: expect.any(String),
        decision_id: 'kept',
        event_id: 'keep-event',
        name: 'My retained momentum',
      },
      expect.any(AbortSignal)
    )
  )
  expect(researchChosenSetups.use).not.toHaveBeenCalled()
  expect(researchChosenSetups.replay).not.toHaveBeenCalled()
  expect(screen.getByLabelText('Location')).toHaveTextContent('view=overview')
})

it('does not let an obsolete Keep event replace the current choice', async () => {
  vi.mocked(researchChosenSetups.context).mockResolvedValue({
    ...context,
    eligibility: {
      ...context.eligibility!,
      available: false,
      reason: 'The decision has changed. Open its current revision.',
    },
  })
  const user = userEvent.setup()
  mount(<ChooseResearchSetup experimentId="idea" event={event} readOnly={false} />)
  await user.click(screen.getByRole('button', { name: 'Choose setup' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByText('The decision has changed. Open its current revision.')
  expect(within(dialog).getByRole('button', { name: 'Choose setup' })).toBeDisabled()
  expect(researchChosenSetups.choose).not.toHaveBeenCalled()
})

it('retains the same choice request after an uncertain response', async () => {
  vi.mocked(researchChosenSetups.choose).mockRejectedValueOnce(new Error('Connection interrupted'))
  const user = userEvent.setup()
  mount(<ChooseResearchSetup experimentId="idea" event={event} readOnly={false} />)
  await user.click(screen.getByRole('button', { name: 'Choose setup' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByLabelText('Setup name')
  await user.click(within(dialog).getByRole('button', { name: 'Choose setup' }))
  await within(dialog).findByRole('alert')
  await user.click(within(dialog).getByRole('button', { name: 'Choose setup' }))
  await waitFor(() => expect(researchChosenSetups.choose).toHaveBeenCalledTimes(2))
  const calls = vi.mocked(researchChosenSetups.choose).mock.calls
  expect(calls[1][1]).toEqual(calls[0][1])
})

it('aborts a choice when the account changes and ignores its late response', async () => {
  let complete!: (value: { choice: ChosenSetup; revision: number; reused: boolean }) => void
  vi.mocked(researchChosenSetups.choose).mockReturnValue(
    new Promise((resolve) => {
      complete = resolve
    })
  )
  const user = userEvent.setup()
  mount(<ChooseResearchSetup experimentId="idea" event={event} readOnly={false} />)
  await user.click(screen.getByRole('button', { name: 'Choose setup' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByLabelText('Setup name')
  await user.click(within(dialog).getByRole('button', { name: 'Choose setup' }))
  const signal = vi.mocked(researchChosenSetups.choose).mock.calls[0][2]!
  act(() => useAuthStore.setState({ user: { username: 'bob', broker: 'fixture' } }))
  expect(signal.aborted).toBe(true)
  await act(async () => complete({ choice, revision: 8, reused: false }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('reopens the choice without applying it and keeps exact replay separate', async () => {
  const onUse = vi.fn(),
    onReplay = vi.fn().mockResolvedValue(undefined)
  const user = userEvent.setup()
  mount(
    <ResearchChosenSetup experimentId="idea" readOnly={false} onUse={onUse} onReplay={onReplay} />
  )
  await screen.findByRole('heading', { name: 'Momentum chosen' })
  expect(onUse).not.toHaveBeenCalled()
  expect(onReplay).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Replay exact' }))
  await waitFor(() =>
    expect(onReplay).toHaveBeenCalledWith(choice, 7, expect.any(String), expect.any(AbortSignal))
  )
  expect(onUse).not.toHaveBeenCalled()
})

it('retains evidence access when a chosen candidate is subsequently rejected', async () => {
  vi.mocked(researchChosenSetups.context).mockResolvedValue({
    ...context,
    current: {
      ...choice,
      usable: false,
      current_decision_state: 'reject',
      unavailable_reason: 'The current decision is Reject.',
    },
  })
  const user = userEvent.setup()
  mount(
    <ResearchChosenSetup experimentId="idea" readOnly={false} onUse={vi.fn()} onReplay={vi.fn()} />
  )
  await screen.findByText('The current decision is Reject.')
  expect(screen.getByRole('button', { name: 'Use setup' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Replay exact' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'Review evidence' }))
  expect(screen.getByLabelText('Location')).toHaveTextContent('decision_event=keep-event')
})

it('opens the existing rules for reuse without applying until explicitly requested', async () => {
  const user = userEvent.setup(),
    onUse = vi.fn().mockResolvedValue(undefined)
  mount(
    <ReuseChosenSetup
      owner="alice"
      experimentId="idea"
      choice={choice}
      readOnly={false}
      onUse={onUse}
    />
  )
  await user.click(screen.getByRole('button', { name: 'Use setup' }))
  await screen.findByLabelText('Signals for Momentum')
  expect(onUse).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Open setup' }))
  expect(onUse).toHaveBeenCalledWith(
    {
      choice_id: 'choice',
      mode: 'backtest',
      sources: {},
      revision: 7,
      request_id: expect.any(String),
    },
    expect.any(AbortSignal)
  )
})

it('replaces one CSV on the same strategy and displays the reviewed before/after dates', async () => {
  const newer = {
    ...source,
    id: 'new-signals',
    receipt: { ...source.receipt, filename: 'new.csv', signal_count: 15, date_to: '2026-09-10' },
  }
  vi.mocked(portfolioResearch.upload).mockResolvedValue(newer)
  vi.mocked(researchChosenSetups.preview).mockImplementation(async (_id, spec) => {
    const result = preview()
    if (spec.sources?.scanner) {
      result.draft.portfolio.strategies[0].source_id = 'new-signals'
      result.draft.sources = { 'new-signals': newer }
      const before = { ...source.receipt, source_id: source.id }
      result.changes.sources = [
        {
          strategy_id: 'scanner',
          name: 'Momentum',
          before,
          after: { ...newer.receipt, source_id: newer.id },
        },
      ]
      result.changes.fields = [{ key: 'date_to', before: '2026-07-10', after: null }]
    }
    return result
  })
  const user = userEvent.setup(),
    onUse = vi.fn().mockResolvedValue(undefined)
  mount(
    <ReuseChosenSetup
      owner="alice"
      experimentId="idea"
      choice={choice}
      readOnly={false}
      onUse={onUse}
    />
  )
  await user.click(screen.getByRole('button', { name: 'Use setup' }))
  await user.upload(
    await screen.findByLabelText('Replace CSV for Momentum'),
    new File(['Date,Symbol\n2026-09-10,AAA'], 'new.csv', { type: 'text/csv' })
  )
  await screen.findByText('Trading rules and allocations unchanged.')
  expect(screen.getByRole('region', { name: 'Input changes' })).toHaveTextContent('10 signals →')
  expect(screen.getByRole('region', { name: 'Input changes' })).toHaveTextContent('15 signals')
  expect(screen.getByText('Date range: all signals in the selected files.')).toBeInTheDocument()
  expect(screen.getByLabelText('Signals for Momentum')).toHaveValue('new-signals')
  await user.click(screen.getByRole('button', { name: 'Open setup' }))
  expect(onUse).toHaveBeenCalledWith(
    expect.objectContaining({ sources: { scanner: 'new-signals' }, choice_id: 'choice' }),
    expect.any(AbortSignal)
  )
})

it('opens refinement as a separate explicit preview', async () => {
  const user = userEvent.setup()
  mount(
    <ReuseChosenSetup
      owner="alice"
      experimentId="idea"
      choice={choice}
      readOnly={false}
      onUse={vi.fn()}
    />
  )
  await user.click(screen.getByRole('button', { name: 'Refine search' }))
  await screen.findByText(/Choose which settings to search in Setup/)
  expect(researchChosenSetups.preview).toHaveBeenCalledWith(
    'idea',
    { choice_id: 'choice', mode: 'optimize', sources: {} },
    expect.any(AbortSignal)
  )
  expect(researchChosenSetups.use).not.toHaveBeenCalled()
})

it('cancels CSV reading on dialog close without applying any setup', async () => {
  vi.mocked(portfolioResearch.upload).mockReturnValue(new Promise(() => {}))
  const user = userEvent.setup(),
    onUse = vi.fn()
  mount(
    <ReuseChosenSetup
      owner="alice"
      experimentId="idea"
      choice={choice}
      readOnly={false}
      onUse={onUse}
    />
  )
  await user.click(screen.getByRole('button', { name: 'Use setup' }))
  await user.upload(
    await screen.findByLabelText('Replace CSV for Momentum'),
    new File(['Date,Symbol'], 'new.csv', { type: 'text/csv' })
  )
  expect(screen.getByRole('button', { name: 'Open setup' })).toBeDisabled()
  const signal = vi.mocked(portfolioResearch.upload).mock.calls[0][1]!
  await user.click(screen.getByRole('button', { name: 'Close' }))
  expect(signal.aborted).toBe(true)
  expect(onUse).not.toHaveBeenCalled()
})

it('lets a trader undo an invalid replacement without closing the review or finding an old file page', async () => {
  vi.mocked(portfolioResearch.sources).mockResolvedValue({ items: [], next_offset: 20 })
  vi.mocked(portfolioResearch.upload).mockResolvedValue({ ...source, id: 'invalid-replacement' })
  vi.mocked(researchChosenSetups.preview).mockImplementation(async (_id, spec) => {
    if (spec.sources?.scanner === 'invalid-replacement')
      throw new Error('This CSV cannot be used with the saved setup.')
    return preview()
  })
  const user = userEvent.setup(),
    onUse = vi.fn()
  mount(
    <ReuseChosenSetup
      owner="alice"
      experimentId="idea"
      choice={choice}
      readOnly={false}
      onUse={onUse}
    />
  )
  await user.click(screen.getByRole('button', { name: 'Use setup' }))
  await user.upload(
    await screen.findByLabelText('Replace CSV for Momentum'),
    new File(['Date,Symbol'], 'new.csv', { type: 'text/csv' })
  )
  await screen.findByText('This CSV cannot be used with the saved setup.')
  expect(screen.getByRole('button', { name: 'Open setup' })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('Signals for Momentum'), 'old-signals')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Open setup' })).toBeEnabled())
  expect(screen.getByLabelText('Signals for Momentum')).toHaveValue('old-signals')
  expect(onUse).not.toHaveBeenCalled()
})

it('keeps the exact pending Choose body after an uncertain response and refreshed revision', async () => {
  vi.mocked(researchChosenSetups.choose).mockRejectedValueOnce(new Error('Response lost'))
  const user = userEvent.setup()
  mount(<ChooseResearchSetup experimentId="idea" event={event} readOnly={false} />)
  await user.click(screen.getByRole('button', { name: 'Choose setup' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByLabelText('Setup name')
  await user.click(within(dialog).getByRole('button', { name: 'Choose setup' }))
  await within(dialog).findByText('Response lost')
  const initialBody = vi.mocked(researchChosenSetups.choose).mock.calls[0][1]
  vi.mocked(researchChosenSetups.context).mockResolvedValue({ ...context, revision: 8 })
  await user.click(within(dialog).getByRole('button', { name: 'Refresh' }))
  await waitFor(() =>
    expect(within(dialog).getByRole('button', { name: 'Choose setup' })).toBeEnabled()
  )
  await user.click(within(dialog).getByRole('button', { name: 'Choose setup' }))
  await waitFor(() => expect(researchChosenSetups.choose).toHaveBeenCalledTimes(2))
  expect(vi.mocked(researchChosenSetups.choose).mock.calls[1][1]).toEqual(initialBody)
})

it('never resends an accepted choice when refreshing the workspace fails', async () => {
  afterChoice.mockRejectedValueOnce(new Error('Could not refresh saved setup'))
  const user = userEvent.setup()
  mount(<ChooseResearchSetup experimentId="idea" event={event} readOnly={false} />)
  await user.click(screen.getByRole('button', { name: 'Choose setup' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByLabelText('Setup name')
  await user.click(within(dialog).getByRole('button', { name: 'Choose setup' }))
  await within(dialog).findByText('Could not refresh saved setup')
  expect(within(dialog).getByLabelText('Setup name')).toBeDisabled()
  await user.click(within(dialog).getByRole('button', { name: 'Open overview' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(researchChosenSetups.choose).toHaveBeenCalledTimes(1)
  expect(afterChoice).toHaveBeenCalledTimes(2)
})

it('waits for the current draft save before opening eligibility or reuse preview', async () => {
  let saved!: () => void
  beforeChange.mockReturnValue(
    new Promise<void>((resolve) => {
      saved = resolve
    })
  )
  const user = userEvent.setup()
  const chooseView = mount(
    <ChooseResearchSetup experimentId="idea" event={event} readOnly={false} />
  )
  await user.click(screen.getByRole('button', { name: 'Choose setup' }))
  expect(researchChosenSetups.context).not.toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await act(async () => saved())
  await screen.findByRole('dialog')
  chooseView.unmount()
  beforeChange.mockReturnValue(
    new Promise<void>((resolve) => {
      saved = resolve
    })
  )
  mount(
    <ReuseChosenSetup
      owner="alice"
      experimentId="idea"
      choice={choice}
      readOnly={false}
      onUse={vi.fn()}
    />
  )
  await user.click(screen.getByRole('button', { name: 'Use setup' }))
  expect(researchChosenSetups.preview).not.toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await act(async () => saved())
  await screen.findByLabelText('Signals for Momentum')
  expect(researchChosenSetups.preview).toHaveBeenCalledTimes(1)
})

it('leaves the current draft untouched and shows a failed pre-open save', async () => {
  beforeChange.mockRejectedValue(new Error('Resolve your draft conflict first'))
  const user = userEvent.setup()
  mount(
    <ReuseChosenSetup
      owner="alice"
      experimentId="idea"
      choice={choice}
      readOnly={false}
      onUse={vi.fn()}
    />
  )
  await user.click(screen.getByRole('button', { name: 'Refine search' }))
  await screen.findByRole('alert')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(researchChosenSetups.preview).not.toHaveBeenCalled()
})

it('retries Use with its exact pending body after refreshing a newer saved revision', async () => {
  const onUse = vi
    .fn()
    .mockRejectedValueOnce(new Error('Response lost'))
    .mockResolvedValue(undefined)
  const user = userEvent.setup()
  mount(
    <ReuseChosenSetup
      owner="alice"
      experimentId="idea"
      choice={choice}
      readOnly={false}
      onUse={onUse}
    />
  )
  await user.click(screen.getByRole('button', { name: 'Use setup' }))
  await screen.findByLabelText('Signals for Momentum')
  await user.click(screen.getByRole('button', { name: 'Open setup' }))
  await screen.findByText('Response lost')
  const initialBody = onUse.mock.calls[0][0]
  vi.mocked(researchChosenSetups.preview).mockResolvedValue({ ...preview(), revision: 8 })
  await user.click(screen.getByRole('button', { name: 'Refresh' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Open setup' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Open setup' }))
  await waitFor(() => expect(onUse).toHaveBeenCalledTimes(2))
  expect(onUse.mock.calls[1][0]).toEqual(initialBody)
})

it('starts a new request only after a definitive revision conflict is explicitly refreshed', async () => {
  const onUse = vi
    .fn()
    .mockRejectedValueOnce({
      isAxiosError: true,
      response: {
        status: 409,
        data: { code: 'revision_conflict', message: 'Saved setup changed' },
      },
    })
    .mockResolvedValue(undefined)
  const user = userEvent.setup()
  mount(
    <ReuseChosenSetup
      owner="alice"
      experimentId="idea"
      choice={choice}
      readOnly={false}
      onUse={onUse}
    />
  )
  await user.click(screen.getByRole('button', { name: 'Use setup' }))
  await screen.findByLabelText('Signals for Momentum')
  await user.click(screen.getByRole('button', { name: 'Open setup' }))
  await screen.findByText('Saved setup changed')
  vi.mocked(researchChosenSetups.preview).mockResolvedValue({ ...preview(), revision: 9 })
  await user.click(screen.getByRole('button', { name: 'Refresh' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Open setup' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Open setup' }))
  await waitFor(() => expect(onUse).toHaveBeenCalledTimes(2))
  expect(onUse.mock.calls[1][0].revision).toBe(9)
  expect(onUse.mock.calls[1][0].request_id).not.toBe(onUse.mock.calls[0][0].request_id)
})

it('retries exact replay with its original revision and request after context refetch', async () => {
  const onReplay = vi
    .fn()
    .mockRejectedValueOnce(new Error('Response lost'))
    .mockResolvedValue(undefined)
  const user = userEvent.setup()
  const { client } = mount(
    <ResearchChosenSetup experimentId="idea" readOnly={false} onUse={vi.fn()} onReplay={onReplay} />
  )
  await screen.findByRole('heading', { name: 'Momentum chosen' })
  await user.click(screen.getByRole('button', { name: 'Replay exact' }))
  await screen.findByText('Response lost')
  const original = onReplay.mock.calls[0]
  vi.mocked(researchChosenSetups.context).mockResolvedValue({ ...context, revision: 9 })
  await act(async () => {
    await client.refetchQueries({
      queryKey: ['research-chosen-setups', 'alice', 'idea', 'current'],
    })
  })
  const beforeRetry = vi.mocked(researchChosenSetups.context).mock.calls.length
  await user.click(screen.getByRole('button', { name: 'Replay exact' }))
  await waitFor(() => expect(onReplay).toHaveBeenCalledTimes(2))
  expect(onReplay.mock.calls[1].slice(0, 3)).toEqual(original.slice(0, 3))
  expect(researchChosenSetups.context).toHaveBeenCalledTimes(beforeRetry)
  expect(beforeChange).toHaveBeenCalledTimes(1)
})

it('freezes the first replay revision after saving the current draft', async () => {
  beforeChange.mockImplementation(async () => {
    vi.mocked(researchChosenSetups.context).mockResolvedValue({ ...context, revision: 12 })
  })
  const onReplay = vi.fn().mockResolvedValue(undefined)
  const user = userEvent.setup()
  mount(
    <ResearchChosenSetup experimentId="idea" readOnly={false} onUse={vi.fn()} onReplay={onReplay} />
  )
  await screen.findByRole('heading', { name: 'Momentum chosen' })
  await user.click(screen.getByRole('button', { name: 'Replay exact' }))
  await waitFor(() =>
    expect(onReplay).toHaveBeenCalledWith(choice, 12, expect.any(String), expect.any(AbortSignal))
  )
})

it('cancels an old choice upload and removes its source mapping when the current choice changes', async () => {
  let uploaded!: (value: typeof source) => void
  vi.mocked(portfolioResearch.upload).mockReturnValue(
    new Promise((resolve) => {
      uploaded = resolve
    })
  )
  const user = userEvent.setup()
  const { client } = mount(
    <ResearchChosenSetup experimentId="idea" readOnly={false} onUse={vi.fn()} onReplay={vi.fn()} />
  )
  await screen.findByRole('heading', { name: 'Momentum chosen' })
  await user.click(screen.getByRole('button', { name: 'Use setup' }))
  await user.upload(
    await screen.findByLabelText('Replace CSV for Momentum'),
    new File(['Date,Symbol'], 'new.csv', { type: 'text/csv' })
  )
  const signal = vi.mocked(portfolioResearch.upload).mock.calls[0][1]!
  const next = { ...choice, id: 'new-choice', name: 'Replacement choice' }
  await act(async () => {
    client.setQueryData(['research-chosen-setups', 'alice', 'idea', 'current'], {
      ...context,
      revision: 9,
      current: next,
    })
  })
  await screen.findByRole('heading', { name: 'Replacement choice' })
  expect(signal.aborted).toBe(true)
  await act(async () => uploaded({ ...source, id: 'late-signals' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Use setup' }))
  await waitFor(() =>
    expect(researchChosenSetups.preview).toHaveBeenLastCalledWith(
      'idea',
      { choice_id: 'new-choice', mode: 'backtest', sources: {} },
      expect.any(AbortSignal)
    )
  )
})

it('aborts replay for a replaced choice and ignores the old response', async () => {
  let completed!: () => void
  const onReplay = vi.fn().mockReturnValue(
    new Promise<void>((resolve) => {
      completed = resolve
    })
  )
  const user = userEvent.setup()
  const { client } = mount(
    <ResearchChosenSetup experimentId="idea" readOnly={false} onUse={vi.fn()} onReplay={onReplay} />
  )
  await screen.findByRole('heading', { name: 'Momentum chosen' })
  await user.click(screen.getByRole('button', { name: 'Replay exact' }))
  await waitFor(() => expect(onReplay).toHaveBeenCalledTimes(1))
  const signal = onReplay.mock.calls[0][3] as AbortSignal
  await act(async () => {
    client.setQueryData(['research-chosen-setups', 'alice', 'idea', 'current'], {
      ...context,
      revision: 9,
      current: { ...choice, id: 'new-choice', name: 'Replacement choice' },
    })
  })
  await screen.findByRole('heading', { name: 'Replacement choice' })
  expect(signal.aborted).toBe(true)
  await act(async () => completed())
  expect(screen.getByRole('button', { name: 'Replay exact' })).toBeEnabled()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
