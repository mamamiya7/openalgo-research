import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  researchStudyContinuation,
  type StudyContinuationContext,
} from '@/api/researchStudyContinuation'
import { ResearchWorkspaceMutations } from '@/hooks/useResearchWorkspaceMutations'
import { useAuthStore } from '@/stores/authStore'
import { StudyContinuation } from './StudyContinuation'

vi.mock('@/api/researchStudyContinuation', () => ({
  researchStudyContinuation: { context: vi.fn(), extend: vi.fn() },
}))

const beforeChange = vi.fn()
const afterChoice = vi.fn()
const clients: QueryClient[] = []
const artifact = 'a'.repeat(64)
const context: StudyContinuationContext = {
  revision: 7,
  parent_result_artifact: artifact,
  current_proposed: 100,
  max_total: 1000,
  available: true,
  reason: null,
}
type ExtensionResponse = Awaited<ReturnType<typeof researchStudyContinuation.extend>>
// The wrapper uses only the saved job identity from this response.
const accepted = { job: { id: 'continued-study' } } as ExtensionResponse
const original = 'experiment=idea&view=studies&job=original-study&trial=18&chart=importance'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function Location() {
  const location = useLocation()
  return <output aria-label="Location">{location.search}</output>
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  const props = {
    experimentId: 'idea',
    jobId: 'original-study',
    resultArtifact: artifact,
    studyName: 'Momentum study',
    readOnly: false,
  }
  const element = (overrides: Partial<typeof props> = {}) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/scanner-research?${original}`]}>
        <ResearchWorkspaceMutations.Provider value={{ beforeChange, afterChoice }}>
          <StudyContinuation {...props} {...overrides} />
        </ResearchWorkspaceMutations.Provider>
        <Location />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return { ...render(element()), client, element }
}

async function open(user = userEvent.setup()) {
  await user.click(screen.getByRole('button', { name: 'Add trials' }))
  return screen.findByRole('dialog', { name: 'Add trials' })
}

async function confirm(user = userEvent.setup(), amount?: string) {
  const dialog = screen.getByRole('dialog', { name: 'Add trials' })
  if (amount) {
    const input = within(dialog).getByRole('spinbutton', { name: 'Additional trials' })
    await user.clear(input)
    await user.type(input, amount)
  }
  await user.click(within(dialog).getByRole('button', { name: 'Add trials' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  beforeChange.mockReset().mockResolvedValue(undefined)
  afterChoice.mockReset().mockResolvedValue(undefined)
  vi.mocked(researchStudyContinuation.context)
    .mockReset()
    .mockResolvedValue({ ...context })
  vi.mocked(researchStudyContinuation.extend).mockReset().mockResolvedValue(accepted)
  useAuthStore.setState({ user: { username: 'alice', broker: 'fixture' } })
})

afterEach(() => {
  cleanup()
  for (const client of clients) client.clear()
  clients.length = 0
  useAuthStore.setState({ user: null })
})

describe('integrated study continuation journey', () => {
  it('reads eligibility only on click, waits for the draft save, and submits only on confirmation', async () => {
    const save = deferred<void>()
    beforeChange.mockReturnValue(save.promise)
    const user = userEvent.setup()
    mount()
    expect(researchStudyContinuation.context).not.toHaveBeenCalled()
    expect(researchStudyContinuation.extend).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Add trials' }))
    expect(beforeChange).toHaveBeenCalledOnce()
    expect(researchStudyContinuation.context).not.toHaveBeenCalled()
    await act(async () => save.resolve())
    await screen.findByRole('dialog', { name: 'Add trials' })
    expect(researchStudyContinuation.context).toHaveBeenCalledWith(
      'idea',
      'original-study',
      expect.any(AbortSignal)
    )
    expect(researchStudyContinuation.extend).not.toHaveBeenCalled()
    await confirm(user, '40')
    expect(researchStudyContinuation.extend).toHaveBeenCalledWith(
      'idea',
      'original-study',
      {
        revision: 7,
        parent_result_artifact: artifact,
        additional_trials: 40,
        request_id: expect.any(String),
      },
      expect.any(AbortSignal)
    )
    expect(vi.mocked(researchStudyContinuation.extend).mock.calls[0][2].request_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
    )
  })

  it('refuses context for a different saved result artifact', async () => {
    vi.mocked(researchStudyContinuation.context).mockResolvedValue({
      ...context,
      parent_result_artifact: 'b'.repeat(64),
    })
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: 'Add trials' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Reopen this study before adding trials.'
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(researchStudyContinuation.extend).not.toHaveBeenCalled()
  })

  it('shows an unavailable-study reason without starting work', async () => {
    vi.mocked(researchStudyContinuation.context).mockResolvedValue({
      ...context,
      available: false,
      reason: 'This study has reached its trial limit.',
    })
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: 'Add trials' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This study has reached its trial limit.'
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(researchStudyContinuation.extend).not.toHaveBeenCalled()
  })

  it('does not read the study if pending draft changes cannot be saved', async () => {
    beforeChange.mockRejectedValue(new Error('Resolve your draft changes first.'))
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: 'Add trials' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Resolve your draft changes first.')
    expect(researchStudyContinuation.context).not.toHaveBeenCalled()
    expect(researchStudyContinuation.extend).not.toHaveBeenCalled()
  })

  it.each([
    new Error('Connection interrupted'),
    { isAxiosError: true, response: { status: 503, data: { message: 'Service unavailable' } } },
  ])('retries the exact original body after an uncertain response and closing the dialog', async (error) => {
    vi.mocked(researchStudyContinuation.extend).mockRejectedValueOnce(error)
    const user = userEvent.setup()
    mount()
    await open(user)
    await confirm(user, '75')
    await screen.findByRole('alert')
    const first = vi.mocked(researchStudyContinuation.extend).mock.calls[0][2]
    expect(screen.getByRole('spinbutton')).toHaveAttribute('readonly')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    // A just-accepted continuation may make new admission unavailable. Recovery
    // must use the saved UUID rather than asking to admit a second request.
    vi.mocked(researchStudyContinuation.context).mockResolvedValue({ ...context, available: false })
    await open(user)
    expect(screen.getByRole('spinbutton')).toHaveValue(75)
    await confirm(user)
    expect(researchStudyContinuation.context).toHaveBeenCalledOnce()
    expect(researchStudyContinuation.extend).toHaveBeenCalledTimes(2)
    expect(vi.mocked(researchStudyContinuation.extend).mock.calls[1][2]).toBe(first)
    await waitFor(() =>
      expect(screen.getByLabelText('Location')).toHaveTextContent('job=continued-study')
    )
  })

  it('does not post twice when the job was accepted but workspace reconciliation failed', async () => {
    afterChoice.mockRejectedValueOnce(new Error('Refresh interrupted'))
    const user = userEvent.setup()
    mount()
    await open(user)
    await confirm(user, '60')
    expect(await screen.findByRole('alert')).toHaveTextContent('Refresh interrupted')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await open(user)
    expect(screen.getByRole('spinbutton')).toHaveValue(60)
    await confirm(user)
    expect(researchStudyContinuation.extend).toHaveBeenCalledOnce()
    expect(afterChoice).toHaveBeenCalledTimes(2)
    await waitFor(() =>
      expect(screen.getByLabelText('Location')).toHaveTextContent('job=continued-study')
    )
  })

  it('aborts on dismissal and ignores a late accepted response, then recovers the same request', async () => {
    const pending = deferred<ExtensionResponse>()
    vi.mocked(researchStudyContinuation.extend).mockReturnValueOnce(pending.promise)
    const user = userEvent.setup()
    mount()
    await open(user)
    await confirm(user, '80')
    const call = vi.mocked(researchStudyContinuation.extend).mock.calls[0]
    expect(screen.getByRole('button', { name: 'Starting…' })).toBeDisabled()
    fireEvent.submit(screen.getByRole('spinbutton').closest('form')!)
    expect(researchStudyContinuation.extend).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(call[3]?.aborted).toBe(true)
    await act(async () => pending.resolve(accepted))
    expect(afterChoice).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Location')).toHaveTextContent(`?${original}`)
    await open(user)
    await confirm(user)
    expect(vi.mocked(researchStudyContinuation.extend).mock.calls[1][2]).toBe(call[2])
  })

  it.each([
    'readOnly',
    'unmount',
  ] as const)('aborts an eligibility read on %s and ignores its response', async (action) => {
    const pending = deferred<StudyContinuationContext>()
    vi.mocked(researchStudyContinuation.context).mockReturnValue(pending.promise)
    const user = userEvent.setup()
    const view = mount()
    await user.click(screen.getByRole('button', { name: 'Add trials' }))
    const signal = vi.mocked(researchStudyContinuation.context).mock.calls[0][2]
    if (action === 'readOnly') view.rerender(view.element({ readOnly: true }))
    else view.unmount()
    expect(signal?.aborted).toBe(true)
    await act(async () => pending.resolve(context))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(researchStudyContinuation.extend).not.toHaveBeenCalled()
    if (action === 'readOnly')
      expect(screen.getByRole('button', { name: 'Add trials' })).toBeDisabled()
  })

  it('preserves the exact pending request across a temporary read-only change', async () => {
    const pending = deferred<ExtensionResponse>()
    vi.mocked(researchStudyContinuation.extend).mockReturnValueOnce(pending.promise)
    const user = userEvent.setup()
    const view = mount()
    await open(user)
    await confirm(user, '70')
    const call = vi.mocked(researchStudyContinuation.extend).mock.calls[0]
    view.rerender(view.element({ readOnly: true }))
    expect(call[3]?.aborted).toBe(true)
    await act(async () => pending.resolve(accepted))
    expect(afterChoice).not.toHaveBeenCalled()
    view.rerender(view.element())
    await open(user)
    await confirm(user)
    expect(vi.mocked(researchStudyContinuation.extend).mock.calls[1][2]).toBe(call[2])
  })

  it('aborts a pending submission on unmount without reconciling a late response', async () => {
    const pending = deferred<ExtensionResponse>()
    vi.mocked(researchStudyContinuation.extend).mockReturnValue(pending.promise)
    const user = userEvent.setup()
    const view = mount()
    await open(user)
    await confirm(user)
    const signal = vi.mocked(researchStudyContinuation.extend).mock.calls[0][3]
    view.unmount()
    expect(signal?.aborted).toBe(true)
    await act(async () => pending.resolve(accepted))
    expect(afterChoice).not.toHaveBeenCalled()
  })

  it('aborts on account change and drops the previous account’s pending request', async () => {
    const pending = deferred<ExtensionResponse>()
    vi.mocked(researchStudyContinuation.extend).mockReturnValueOnce(pending.promise)
    const user = userEvent.setup()
    mount()
    await open(user)
    await confirm(user, '90')
    const call = vi.mocked(researchStudyContinuation.extend).mock.calls[0]
    act(() => useAuthStore.setState({ user: { username: 'bob', broker: 'fixture' } }))
    expect(call[3]?.aborted).toBe(true)
    await act(async () => pending.resolve(accepted))
    expect(afterChoice).not.toHaveBeenCalled()
    await open(user)
    expect(researchStudyContinuation.context).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('spinbutton')).toHaveValue(25)
    expect(screen.getByRole('spinbutton')).not.toHaveAttribute('readonly')
  })

  it.each([
    400, 404, 409,
  ])('allows a fresh corrected request after a definitive %s rejection', async (status) => {
    vi.mocked(researchStudyContinuation.extend).mockRejectedValueOnce({
      isAxiosError: true,
      response: { status, data: { message: 'Reopen the current study.' } },
    })
    const user = userEvent.setup()
    mount()
    await open(user)
    await confirm(user, '80')
    await screen.findByRole('alert')
    const first = vi.mocked(researchStudyContinuation.extend).mock.calls[0][2]
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await open(user)
    expect(screen.getByRole('spinbutton')).not.toHaveAttribute('readonly')
    await confirm(user, '40')
    const second = vi.mocked(researchStudyContinuation.extend).mock.calls[1][2]
    expect(second.additional_trials).toBe(40)
    expect(second.request_id).not.toBe(first.request_id)
    expect(researchStudyContinuation.context).toHaveBeenCalledTimes(2)
  })

  it('opens the saved child study with the source view preserved and refreshes the library', async () => {
    const user = userEvent.setup()
    const { client } = mount()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    await open(user)
    await confirm(user)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const params = new URLSearchParams(screen.getByLabelText('Location').textContent!)
    expect(params.get('experiment')).toBe('idea')
    expect(params.get('view')).toBe('studies')
    expect(params.get('job')).toBe('continued-study')
    expect(params.get('return_research')).toBe(original)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['research-library', 'alice'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['research-experiment', 'alice', 'idea'] })
    expect(afterChoice).toHaveBeenCalledOnce()
  })
})
