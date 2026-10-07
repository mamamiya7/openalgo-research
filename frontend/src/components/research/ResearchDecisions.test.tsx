import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError } from 'axios'
import { axe } from 'jest-axe'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PortfolioResult } from '@/api/portfolioResearch'
import {
  type DecisionContext,
  type DecisionEvent,
  type EvidenceOpeningIntent,
  researchDecisions,
} from '@/api/researchDecisions'
import { useAuthStore } from '@/stores/authStore'
import { CandidateDecision } from './CandidateDecision'
import { EvidenceOpened, EvidenceUse } from './DecisionEvidence'
import { PortfolioResults } from './PortfolioResults'
import { ResearchDecisions } from './ResearchDecisions'

vi.mock('@/api/researchDecisions', async (original) => ({
  ...(await original<typeof import('@/api/researchDecisions')>()),
  researchDecisions: {
    context: vi.fn(),
    save: vi.fn(),
    list: vi.fn(),
    history: vi.fn(),
    event: vi.fn(),
    report: vi.fn(),
    preview: vi.fn(),
    opened: vi.fn(),
  },
}))
vi.mock('./PortfolioResults', () => ({
  PortfolioResults: vi.fn(() => <div>Exact saved financial report</div>),
}))
const later = {
  id: 'later/+?pin',
  job_id: 'later-job',
  report_id: 'later-report',
  view: 'primary' as const,
  dates: { from: '2026-07-01', to: '2026-08-01' },
  available: true,
}
const evidence = {
  reservation: { from: '2026-07-01', to: '2026-08-01' },
  reservation_status: 'reserved_in_setup' as const,
  calculation: 'recorded' as const,
  opened_at: null,
  later_used_for_decision: false,
  coverage: 'legacy_unknown' as const,
  overlap: 'not_found' as const,
}
const event = (patch: Partial<DecisionEvent> = {}): DecisionEvent => ({
  id: 'event-1',
  decision_id: 'd',
  revision: 1,
  supersedes_event_id: null,
  state: 'keep',
  reason: 'Promising with costs',
  created_at: 1000,
  candidate_name: 'Alternative target',
  comparison_name: 'Original comparison',
  comparison_id: 'c',
  member_id: 'm',
  source_job_id: 'study',
  source_result_artifact: 'source-result',
  config_id: 'config',
  period: 'selection',
  trial_number: 2,
  proposal_number: 4,
  is_objective_winner: false,
  evaluation: null,
  evidence_use: evidence,
  ...patch,
})
const context = (patch: Partial<DecisionContext> = {}): DecisionContext => ({
  target: { comparison_id: 'c', member_id: 'm' },
  current: null,
  evidence_use: evidence,
  evaluation: { items: [later], next_offset: null, total: null },
  archived: false,
  ...patch,
})
const result: PortfolioResult = {
  summary: { net_return_pct: 12 },
  config: { initial_capital: 100 },
  strategies: [],
  per_strategy: [],
  ledger: [],
  equity_curve: [],
}
const report = {
  available: true,
  job: { id: 'frozen', status: 'completed' as const, progress: 100, created_at: 1 },
  result,
}
const clients: QueryClient[] = []
function Location() {
  const location = useLocation()
  return <output aria-label="Location">{location.search}</output>
}
function mount(node: ReactNode, url = '/?experiment=e&view=decisions') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        {node}
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
function candidate(readOnly = false, onHistory = vi.fn()) {
  return (
    <CandidateDecision
      experimentId="e"
      target={{ comparison_id: 'c', member_id: 'm' }}
      name="Alternative target"
      readOnly={readOnly}
      onHistory={onHistory}
    />
  )
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { username: 'owner' } })
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  vi.mocked(researchDecisions.context).mockResolvedValue(context())
  vi.mocked(researchDecisions.save).mockImplementation(async (_e, _target, data) => {
    const saved = event({ state: data.state, reason: data.reason, revision: data.revision + 1 })
    return {
      decision: { id: 'd', revision: saved.revision, current: saved, archived: false },
      event: saved,
      reused: false,
    }
  })
  vi.mocked(researchDecisions.list).mockResolvedValue({
    items: [{ id: 'd', revision: 1, current: event(), archived: false }],
    next_offset: null,
    total: 1,
  })
  vi.mocked(researchDecisions.history).mockResolvedValue({
    items: [
      event({ id: 'event-2', state: 'revisit', revision: 2, reason: 'Check another period' }),
      event(),
    ],
    next_offset: null,
    total: 2,
  })
  vi.mocked(researchDecisions.event).mockImplementation(async (_e, _d, id) => ({
    event: event({
      id,
      ...(id === 'event-2'
        ? { state: 'revisit', revision: 2, reason: 'Check another period' }
        : {}),
    }),
    archived: false,
    available: true,
  }))
  vi.mocked(researchDecisions.report).mockResolvedValue(report)
  vi.mocked(researchDecisions.preview).mockResolvedValue(report)
  vi.mocked(researchDecisions.opened).mockResolvedValue({ opened_at: 2000, reused: false })
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
})
describe('candidate research decision', () => {
  it('saves directly from a later report with its admitted supporting evidence', async () => {
    const target = { job_id: 'study', config_id: 'alternative' }
    vi.mocked(researchDecisions.context).mockResolvedValue(context({ target }))
    mount(
      <CandidateDecision
        experimentId="e"
        target={target}
        name="Trial 3"
        readOnly={false}
        preferredEvaluationId={later.id}
        onHistory={vi.fn()}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Decision for Trial 3' }))
    await waitFor(() => expect(screen.getByLabelText('Supporting report')).toHaveValue(later.id))
    expect(screen.getByRole('option', { name: 'Selection report only' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Keep', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Save decision' }))
    await waitFor(() =>
      expect(researchDecisions.save).toHaveBeenCalledWith(
        'e',
        target,
        expect.objectContaining({ evaluation_id: later.id, state: 'keep', revision: 0 }),
        expect.any(AbortSignal)
      )
    )
    expect(researchDecisions.opened).not.toHaveBeenCalled()
  })
  it('preserves an existing decision choice when entered from another report', async () => {
    const target = { job_id: 'study', config_id: 'alternative' }
    vi.mocked(researchDecisions.context).mockResolvedValue(
      context({
        target,
        current: { id: 'd', revision: 1, current: event({ evaluation: null }), archived: false },
      })
    )
    mount(
      <CandidateDecision
        experimentId="e"
        target={target}
        name="Trial 3"
        readOnly={false}
        preferredEvaluationId={later.id}
        onHistory={vi.fn()}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Decision for Trial 3' }))
    await waitFor(() => expect(screen.getByLabelText('Supporting report')).toHaveValue(''))
    expect(screen.getByLabelText(/Reason/)).toHaveValue('Promising with costs')
  })
  it('cancels a pending context read when the dialog closes', async () => {
    vi.mocked(researchDecisions.context).mockImplementation(() => new Promise(() => {}))
    mount(candidate())
    await userEvent.click(screen.getByRole('button', { name: 'Decision for Alternative target' }))
    await waitFor(() => expect(researchDecisions.context).toHaveBeenCalledTimes(1))
    const signal = vi.mocked(researchDecisions.context).mock.calls[0][3]!
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(signal.aborted).toBe(true))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  it('reads quietly, requires a choice, previews later evidence only on request and saves exact receipt', async () => {
    mount(candidate())
    expect(researchDecisions.context).not.toHaveBeenCalled()
    expect(researchDecisions.save).not.toHaveBeenCalled()
    expect(researchDecisions.preview).not.toHaveBeenCalled()
    expect(researchDecisions.opened).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Decision for Alternative target' }))
    expect(await screen.findByRole('button', { name: 'Save decision' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Keep', exact: true }))
    await userEvent.type(screen.getByLabelText(/Reason/), 'Retain for review')
    await userEvent.selectOptions(screen.getByLabelText('Supporting report'), later.id)
    expect(researchDecisions.preview).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Open later report' }))
    expect(await screen.findByText('Exact saved financial report')).toBeVisible()
    await waitFor(() => expect(researchDecisions.opened).toHaveBeenCalledTimes(1))
    expect(researchDecisions.opened).toHaveBeenLastCalledWith(
      'e',
      {
        request_id: expect.any(String),
        target: {
          kind: 'comparison_member',
          comparison_id: 'c',
          member_id: 'm',
          evaluation_id: later.id,
        },
      },
      expect.any(AbortSignal)
    )
    const frozen = vi.mocked(PortfolioResults).mock.calls.at(-1)![0]
    expect(frozen).toMatchObject({
      readOnly: true,
      freezeAnalysis: true,
      embedded: true,
      exportUrl: '',
      result,
    })
    await userEvent.click(screen.getByRole('button', { name: 'Back to decision' }))
    expect(screen.getByLabelText(/Reason/)).toHaveValue('Retain for review')
    await act(async () => {
      expect((await axe(document.body)).violations).toEqual([])
    })
    await userEvent.click(screen.getByRole('button', { name: 'Save decision' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(researchDecisions.save).toHaveBeenLastCalledWith(
      'e',
      { comparison_id: 'c', member_id: 'm' },
      {
        request_id: expect.any(String),
        revision: 0,
        state: 'keep',
        reason: 'Retain for review',
        evaluation_id: later.id,
      },
      expect.any(AbortSignal)
    )
  })
  it('keeps a failed draft and retries the same request token', async () => {
    vi.mocked(researchDecisions.save).mockRejectedValueOnce(new Error('network'))
    mount(candidate())
    await userEvent.click(screen.getByRole('button', { name: 'Decision for Alternative target' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Reject', exact: true }))
    await userEvent.type(screen.getByLabelText(/Reason/), 'Too concentrated')
    await userEvent.click(screen.getByRole('button', { name: 'Save decision' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again')
    expect(screen.getByLabelText(/Reason/)).toHaveValue('Too concentrated')
    await userEvent.click(screen.getByRole('button', { name: 'Save decision' }))
    await waitFor(() => expect(researchDecisions.save).toHaveBeenCalledTimes(2))
    expect(vi.mocked(researchDecisions.save).mock.calls[0][2]).toEqual(
      vi.mocked(researchDecisions.save).mock.calls[1][2]
    )
  })
  it('preserves conflict drafts until explicit reload, then uses the latest head revision', async () => {
    vi.mocked(researchDecisions.save).mockRejectedValueOnce(
      new AxiosError('Conflict', 'ERR_BAD_REQUEST', undefined, undefined, {
        status: 409,
        data: { code: 'decision_revision_conflict', message: 'Decision changed elsewhere.' },
      } as never)
    )
    mount(candidate())
    await userEvent.click(screen.getByRole('button', { name: 'Decision for Alternative target' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Revisit', exact: true }))
    await userEvent.type(screen.getByLabelText(/Reason/), 'My unsaved reason')
    await userEvent.click(screen.getByRole('button', { name: 'Save decision' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Decision changed elsewhere')
    expect(screen.getByLabelText(/Reason/)).toHaveValue('My unsaved reason')
    expect(screen.getByRole('button', { name: 'Save decision' })).toBeDisabled()
    vi.mocked(researchDecisions.context).mockResolvedValue(
      context({
        current: {
          id: 'd',
          revision: 3,
          current: event({ revision: 3, reason: 'Saved elsewhere' }),
          archived: false,
        },
      })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Reload saved decision' }))
    await waitFor(() => expect(screen.getByLabelText(/Reason/)).toHaveValue('Saved elsewhere'))
    await userEvent.click(screen.getByRole('button', { name: 'Save decision' }))
    await waitFor(() =>
      expect(vi.mocked(researchDecisions.save).mock.calls.at(-1)![2].revision).toBe(3)
    )
  })
  it('keeps archived decisions readable and aborts a pending write on account change', async () => {
    const view = mount(candidate(true))
    await userEvent.click(screen.getByRole('button', { name: 'Decision for Alternative target' }))
    expect(await screen.findByLabelText(/Reason/)).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save decision' })).not.toBeInTheDocument()
    view.unmount()
    vi.mocked(researchDecisions.save).mockImplementation(() => new Promise(() => {}))
    mount(candidate())
    await userEvent.click(screen.getByRole('button', { name: 'Decision for Alternative target' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Keep', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Save decision' }))
    const signal = vi.mocked(researchDecisions.save).mock.calls.at(-1)![3]!
    act(() => useAuthStore.setState({ user: { username: 'another' } }))
    await waitFor(() => expect(signal.aborted).toBe(true))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
describe('saved decisions and exact history', () => {
  it('selects the new saved revision immediately while preserving its prior history', async () => {
    vi.mocked(researchDecisions.context).mockResolvedValue(
      context({ current: { id: 'd', revision: 1, current: event(), archived: false } })
    )
    vi.mocked(researchDecisions.save).mockResolvedValue({
      decision: {
        id: 'd',
        revision: 2,
        current: event({
          id: 'event-2',
          revision: 2,
          state: 'revisit',
          reason: 'Check another period',
        }),
        archived: false,
      },
      event: event({
        id: 'event-2',
        revision: 2,
        state: 'revisit',
        reason: 'Check another period',
      }),
      reused: false,
    })
    mount(
      <ResearchDecisions experimentId="e" readOnly={false} />,
      '/?experiment=e&view=decisions&decision=d&decision_event=event-1'
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Decision for Alternative target' })
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Revisit', exact: true }))
    await userEvent.clear(screen.getByLabelText(/Reason/))
    await userEvent.type(screen.getByLabelText(/Reason/), 'Check another period')
    await userEvent.click(screen.getByRole('button', { name: 'Save decision' }))
    expect(await screen.findByText('Check another period', { selector: 'p' })).toBeVisible()
    expect(screen.getByLabelText('Location')).toHaveTextContent('decision_event=event-2')
    expect(screen.getByRole('button', { name: 'Open revision 1' })).toBeVisible()
  })
  it('keeps selection evidence openable when only its attached later file is missing', async () => {
    vi.mocked(researchDecisions.event).mockResolvedValue({
      event: event({ evaluation: { ...later, available: false } }),
      archived: false,
      available: false,
      error: 'Later report unavailable',
    })
    mount(
      <ResearchDecisions experimentId="e" readOnly={false} />,
      '/?experiment=e&view=decisions&decision=d&decision_event=event-1'
    )
    expect(await screen.findByRole('button', { name: 'Open report', exact: true })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Open later report' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Open report', exact: true }))
    expect(await screen.findByText('Exact saved financial report')).toBeVisible()
    expect(researchDecisions.report).toHaveBeenLastCalledWith(
      'e',
      'd',
      'event-1',
      'selection',
      expect.any(AbortSignal)
    )
  })
  it('filters bounded current choices and opens immutable historical revisions and reports', async () => {
    mount(<ResearchDecisions experimentId="e" readOnly={false} />)
    await userEvent.selectOptions(screen.getByLabelText('Filter decisions'), 'keep')
    await waitFor(() =>
      expect(researchDecisions.list).toHaveBeenLastCalledWith(
        'e',
        'keep',
        0,
        expect.any(AbortSignal)
      )
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'View decision for Alternative target' })
    )
    expect(await screen.findByText('Promising with costs', { selector: 'p' })).toBeVisible()
    expect(researchDecisions.report).not.toHaveBeenCalled()
    expect(researchDecisions.opened).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Open revision 2' }))
    expect(await screen.findByText('Check another period', { selector: 'p' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Open report', exact: true }))
    expect(await screen.findByText('Exact saved financial report')).toBeVisible()
    expect(researchDecisions.report).toHaveBeenLastCalledWith(
      'e',
      'd',
      'event-2',
      'selection',
      expect.any(AbortSignal)
    )
    await waitFor(() => expect(researchDecisions.opened).toHaveBeenCalledTimes(1))
    expect(vi.mocked(researchDecisions.opened).mock.calls[0][1].target).toEqual({
      kind: 'decision_event',
      decision_id: 'd',
      event_id: 'event-2',
      evidence: 'selection',
    })
    await userEvent.click(screen.getByRole('button', { name: 'Back to decision' }))
    expect(await screen.findByText('Check another period', { selector: 'p' })).toBeVisible()
    expect(screen.getByLabelText('Location')).toHaveTextContent('decision_state=keep')
    await userEvent.click(screen.getByRole('button', { name: 'Open comparison' }))
    expect(screen.getByLabelText('Location')).toHaveTextContent('return_decision_event=event-2')
  })
  it('reopens a bookmarked report without recording a user opening, and freezes all report actions', async () => {
    mount(
      <ResearchDecisions experimentId="e" readOnly />,
      '/?experiment=e&view=decisions&decision=d&decision_event=event-1&decision_report=selection'
    )
    await screen.findByText('Exact saved financial report')
    expect(researchDecisions.opened).not.toHaveBeenCalled()
    expect(vi.mocked(PortfolioResults).mock.calls.at(-1)![0]).toMatchObject({
      readOnly: true,
      freezeAnalysis: true,
      exportUrl: '',
      result,
    })
    expect(researchDecisions.history).not.toHaveBeenCalled()
  })
  it('opens exactly the attached later event without treating generic earlier usage as current evidence', async () => {
    vi.mocked(researchDecisions.event).mockResolvedValue({
      event: event({
        evaluation: later,
        evidence_use: { ...evidence, later_used_for_decision: true },
      }),
      archived: true,
      available: true,
    })
    mount(
      <ResearchDecisions experimentId="e" readOnly />,
      '/?experiment=e&view=decisions&decision=d&decision_event=event-1'
    )
    expect(await screen.findByText(/Later results used for this decision/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Open later report' }))
    await screen.findByText('Exact saved financial report')
    expect(researchDecisions.report).toHaveBeenLastCalledWith(
      'e',
      'd',
      'event-1',
      'evaluation',
      expect.any(AbortSignal)
    )
    await waitFor(() => expect(researchDecisions.opened).toHaveBeenCalled())
    expect(researchDecisions.save).not.toHaveBeenCalled()
  })
  it('does not acknowledge an unavailable report or replace it with a new calculation', async () => {
    vi.mocked(researchDecisions.report).mockResolvedValue({
      available: false,
      error: 'Original evidence unavailable',
      job: null,
      result: null,
    })
    mount(
      <ResearchDecisions experimentId="e" readOnly={false} />,
      '/?experiment=e&view=decisions&decision=d&decision_event=event-1'
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Open report', exact: true }))
    expect(await screen.findByText('Original evidence unavailable')).toBeVisible()
    expect(researchDecisions.opened).not.toHaveBeenCalled()
    expect(PortfolioResults).not.toHaveBeenCalled()
  })
})
describe('truthful evidence opening acknowledgment', () => {
  const intent: EvidenceOpeningIntent = {
    request_id: 'open-request',
    target: { kind: 'comparison_member', comparison_id: 'c', member_id: 'm' },
  }
  it('waits for a visible report, records once, and cancels hidden listeners on unmount', async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    const view = mount(<EvidenceOpened owner="owner" experimentId="e" intent={intent} />)
    expect(researchDecisions.opened).not.toHaveBeenCalled()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    await waitFor(() => expect(researchDecisions.opened).toHaveBeenCalledTimes(1))
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    expect(researchDecisions.opened).toHaveBeenCalledTimes(1)
    view.unmount()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    const hidden = mount(
      <EvidenceOpened
        owner="owner"
        experimentId="e"
        intent={{ ...intent, request_id: 'never-visible' }}
      />
    )
    hidden.unmount()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    expect(researchDecisions.opened).toHaveBeenCalledTimes(1)
  })
  it('retries a failed observation with the same token and aborts outstanding requests', async () => {
    vi.mocked(researchDecisions.opened)
      .mockRejectedValueOnce(new Error('lost network'))
      .mockImplementationOnce(() => new Promise(() => {}))
    const view = mount(<EvidenceOpened owner="owner" experimentId="e" intent={intent} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Retry recording opening' }))
    expect(vi.mocked(researchDecisions.opened).mock.calls[0][1]).toEqual(
      vi.mocked(researchDecisions.opened).mock.calls[1][1]
    )
    const signal = vi.mocked(researchDecisions.opened).mock.calls[1][2]!
    view.unmount()
    expect(signal.aborted).toBe(true)
  })
  it('distinguishes calculation from later use and never calls unknown history untouched', () => {
    mount(<EvidenceUse evidence={{ ...evidence, later_used_for_decision: true }} />)
    const panel = screen.getByText('Evidence history').parentElement!
    expect(within(panel).getByText('Calculation recorded')).toBeInTheDocument()
    expect(within(panel).getByText('Later results have been used in decisions')).toBeInTheDocument()
    expect(panel).not.toHaveTextContent('Later result prepared')
    expect(panel).not.toHaveTextContent('Later results used for this decision')
    expect(panel).not.toHaveTextContent('Untouched')
  })
})
