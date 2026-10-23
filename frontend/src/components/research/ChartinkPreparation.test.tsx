import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type PreparationStatus, researchLibrary } from '@/api/researchLibrary'
import { scannerResearch } from '@/api/scannerResearch'
import { ChartinkPreparation } from './ChartinkPreparation'

vi.mock('@/api/researchLibrary', () => ({
  researchLibrary: { startPreparation: vi.fn(), preparation: vi.fn() },
}))
vi.mock('@/api/scannerResearch', () => ({ scannerResearch: { cancel: vi.fn() } }))
const queued: PreparationStatus = {
  job_id: 'prep',
  status: 'queued',
  progress: 0,
  activity: null,
  ready: false,
  error: null,
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(researchLibrary.startPreparation).mockResolvedValue(queued)
  vi.mocked(researchLibrary.preparation).mockResolvedValue({
    ...queued,
    status: 'completed',
    ready: true,
  })
  vi.mocked(scannerResearch.cancel).mockResolvedValue({} as never)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})
describe('Chartink early preparation', () => {
  it('starts on landing without a Run action and waits for saved settings', async () => {
    const view = render(
      <ChartinkPreparation experimentId="experiment" revision={1} enabled={false} />
    )
    expect(researchLibrary.startPreparation).not.toHaveBeenCalled()
    view.rerender(<ChartinkPreparation experimentId="experiment" revision={2} enabled />)
    await screen.findByText('Price preparation queued')
    expect(researchLibrary.startPreparation).toHaveBeenCalledWith(
      'experiment',
      2,
      expect.any(AbortSignal)
    )
  })
  it('polls to readiness and stops polling after unmount', async () => {
    vi.useFakeTimers()
    const view = render(<ChartinkPreparation experimentId="experiment" revision={1} enabled />)
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(screen.getByText('Prices and research inputs ready')).toBeInTheDocument()
    const signal = vi.mocked(researchLibrary.startPreparation).mock.calls[0][2]!
    view.unmount()
    expect(signal.aborted).toBe(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(researchLibrary.preparation).toHaveBeenCalledTimes(1)
  })
  it('aborts obsolete requests and prepares the new saved revision', async () => {
    const view = render(<ChartinkPreparation experimentId="experiment" revision={1} enabled />)
    await screen.findByText('Price preparation queued')
    const old = vi.mocked(researchLibrary.startPreparation).mock.calls[0][2]!
    view.rerender(<ChartinkPreparation experimentId="experiment" revision={2} enabled />)
    await waitFor(() => expect(researchLibrary.startPreparation).toHaveBeenCalledTimes(2))
    expect(old.aborted).toBe(true)
  })
  it('stops without automatically restarting and can retry', async () => {
    render(<ChartinkPreparation experimentId="experiment" revision={1} enabled />)
    fireEvent.click(await screen.findByRole('button', { name: 'Stop preparation' }))
    await screen.findByText('Background preparation stopped.')
    expect(scannerResearch.cancel).toHaveBeenCalledWith('prep')
    expect(researchLibrary.startPreparation).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Retry preparation' }))
    await waitFor(() => expect(researchLibrary.startPreparation).toHaveBeenCalledTimes(2))
  })
})
