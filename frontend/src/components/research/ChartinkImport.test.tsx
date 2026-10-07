import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  type ChartinkImportPayload,
  chartinkPayload,
  researchChartink,
} from '@/api/researchChartink'
import { useAuthStore } from '@/stores/authStore'
import { ChartinkImport } from './ChartinkImport'
import { ChartinkSource } from './ChartinkSource'

vi.mock('@/api/researchChartink', async (original) => ({
  ...(await original<typeof import('@/api/researchChartink')>()),
  researchChartink: { importHistory: vi.fn() },
}))
const requestId = '28e5788b-92a1-4e34-877a-8fc902e912fd'
const input: ChartinkImportPayload = {
  version: 1,
  request_id: requestId,
  csv_text: 'symbol,date\nINFY,2026-08-03',
  source: {
    url: 'https://chartink.com/screener/breakout',
    title: 'Breakout',
    selected_period: '3 months',
    captured_at: '2026-09-11T06:00:00Z',
    repaints: null,
    export_kind: 'chartink_history_csv',
  },
}
function Location() {
  return (
    <output data-testid="location">
      {useLocation().pathname}
      {useLocation().search}
    </output>
  )
}
function View({ owner = 'account', id = requestId }: { owner?: string; id?: string }) {
  return (
    <StrictMode>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/scanner-research?chartink_import=${id}`]}>
          <ChartinkImport requestId={id} owner={owner} />
          <Location />
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>
  )
}
let client: QueryClient
function deliver(
  payload: unknown = input,
  options: Partial<MessageEventInit> = {},
  envelope: Record<string, unknown> = {}
) {
  fireEvent(
    window,
    new MessageEvent('message', {
      source: window,
      origin: window.location.origin,
      data: { type: 'openalgo:chartink-import', version: 1, requestId, payload, ...envelope },
      ...options,
    })
  )
}
beforeEach(() => {
  vi.resetAllMocks()
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useAuthStore.setState({ user: null })
  vi.spyOn(window, 'postMessage').mockImplementation(() => undefined)
})
afterEach(() => {
  cleanup()
  client.clear()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('Chartink native handoff', () => {
  it('accepts a same-window payload once under StrictMode and constructs the local setup destination', async () => {
    let finish!: (value: Awaited<ReturnType<typeof researchChartink.importHistory>>) => void
    vi.mocked(researchChartink.importHistory).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    render(<View />)
    expect(window.postMessage).toHaveBeenCalledWith(
      { type: 'openalgo:chartink-ready', version: 1, requestId },
      window.location.origin
    )
    deliver()
    deliver()
    expect(researchChartink.importHistory).toHaveBeenCalledTimes(1)
    expect(researchChartink.importHistory).toHaveBeenCalledWith(input, expect.any(AbortSignal))
    expect(screen.getByText('Saving signals and opening your setup…')).toBeVisible()
    await act(async () =>
      finish({
        protocol_version: 1,
        experiment_id: 'a'.repeat(32),
        source_id: 'b'.repeat(32),
        url: 'https://unexpected.example/leave',
        receipt: {} as never,
        reused: false,
      })
    )
    expect(screen.getByTestId('location')).toHaveTextContent(
      `/scanner-research?experiment=${'a'.repeat(32)}&view=setup`
    )
    expect(window.postMessage).toHaveBeenCalledWith(
      {
        type: 'openalgo:chartink-result',
        version: 1,
        requestId,
        ok: true,
        experimentId: 'a'.repeat(32),
      },
      window.location.origin
    )
  })

  it('rejects other windows, origins, requests and protocol versions', () => {
    render(<View />)
    deliver(input, { source: null })
    deliver(input, { source: {} as Window })
    deliver(input, { origin: 'https://chartink.com' })
    deliver(input, {}, { requestId: 'another-request' })
    deliver(input, {}, { version: 2 })
    expect(researchChartink.importHistory).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('responds when the bridge is injected after the page was ready', () => {
    render(<View />)
    vi.mocked(window.postMessage).mockClear()
    deliver(undefined, {}, { type: 'openalgo:chartink-bridge-ready' })
    expect(window.postMessage).toHaveBeenCalledWith(
      { type: 'openalgo:chartink-ready', version: 1, requestId },
      window.location.origin
    )
    expect(researchChartink.importHistory).not.toHaveBeenCalled()
  })

  it('rejects malformed matching exports with one actionable error', () => {
    render(<View />)
    deliver({ ...input, source: { ...input.source, export_kind: 'today_table' } })
    expect(researchChartink.importHistory).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('supported Chartink history export')
    expect(window.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'openalgo:chartink-result', ok: false }),
      window.location.origin
    )
  })

  it.each([
    'draft',
    'A'.repeat(32),
    'a'.repeat(31),
    'a'.repeat(33),
  ])('does not acknowledge or navigate to a non-native experiment ID %s', async (experimentId) => {
    vi.mocked(researchChartink.importHistory).mockResolvedValue({
      protocol_version: 1,
      experiment_id: experimentId,
      source_id: 'b'.repeat(32),
      url: '/scanner-research',
      receipt: {} as never,
      reused: false,
    })
    render(<View />)
    deliver()
    await screen.findByRole('alert')
    expect(screen.getByTestId('location')).toHaveTextContent(`chartink_import=${requestId}`)
    expect(
      vi.mocked(window.postMessage).mock.calls.filter(([event]) => event.ok === true)
    ).toHaveLength(0)
  })

  it('offers recovery immediately when its own bridge reports a missing capture', () => {
    vi.useFakeTimers()
    render(<View />)
    deliver(undefined, {}, { type: 'openalgo:chartink-missing', requestId: 'another' })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    deliver(undefined, {}, { type: 'openalgo:chartink-missing' })
    expect(screen.getByRole('alert')).toHaveTextContent('reopen the extension')
    expect(vi.getTimerCount()).toBe(0)
    deliver()
    expect(researchChartink.importHistory).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('Waiting for your scanner’s history…')).toBeVisible()
  })

  it('retries the exact retained payload after an uncertain response', async () => {
    vi.mocked(researchChartink.importHistory).mockRejectedValue(new Error('network'))
    render(<View />)
    deliver()
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(researchChartink.importHistory).toHaveBeenCalledTimes(2))
    expect(vi.mocked(researchChartink.importHistory).mock.calls[1][0]).toEqual(input)
    expect(
      vi.mocked(window.postMessage).mock.calls.filter(([event]) => event.ok === true)
    ).toHaveLength(0)
  })

  it('times out missing capture and releases listeners and timers on navigation away', () => {
    vi.useFakeTimers()
    const remove = vi.spyOn(window, 'removeEventListener')
    const { unmount } = render(<View />)
    act(() => vi.advanceTimersByTime(15000))
    expect(screen.getByRole('alert')).toHaveTextContent('reopen the extension')
    unmount()
    expect(vi.getTimerCount()).toBe(0)
    expect(remove).toHaveBeenCalledWith('message', expect.any(Function))
    deliver()
    expect(researchChartink.importHistory).not.toHaveBeenCalled()
  })

  it('aborts pending work and requires a retry if the account changes', () => {
    vi.mocked(researchChartink.importHistory).mockReturnValue(new Promise(() => {}))
    const { rerender } = render(<View />)
    deliver()
    const signal = vi.mocked(researchChartink.importHistory).mock.calls[0][1]
    act(() =>
      useAuthStore.setState({
        user: { username: 'another', broker: null, isLoggedIn: true, loginTime: null },
      })
    )
    rerender(<View owner="another" />)
    expect(signal.aborted).toBe(true)
    expect(screen.getByRole('alert')).toHaveTextContent('Your account changed')
    deliver()
    expect(researchChartink.importHistory).toHaveBeenCalledTimes(1)
  })

  it('does not start a handshake from an invalid link', () => {
    render(<View id="broken" />)
    expect(window.postMessage).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('import link is incomplete')
  })
})

describe('Chartink source validation and presentation', () => {
  it.each([
    { source: { ...input.source, url: 'https://chartink.com.attacker.example/screener/test' } },
    { source: { ...input.source, url: 'https://www.chartink.com/screener/test' } },
    { source: { ...input.source, url: 'https://chartink.com:443/screener/test' } },
    { source: { ...input.source, url: 'https://chartink.com/screener/test?period=9' } },
    { source: { ...input.source, url: 'https://chartink.com/screener/test#history' } },
    { source: { ...input.source, url: 'https://chartink.com/screener/test/history' } },
    { source: { ...input.source, url: `https://chartink.com/screener/${'a'.repeat(201)}` } },
    { source: { ...input.source, url: 'javascript:alert(1)' } },
    { source: { ...input.source, title: 'x'.repeat(241) } },
    { source: { ...input.source, title: '   ' } },
    { source: { ...input.source, selected_period: 'x'.repeat(81) } },
    { source: { ...input.source, repaints: 'false' } },
    { source: { ...input.source, captured_at: 'not-a-date' } },
    { source: { ...input.source, captured_at: '2026-09-11T06:00:00' } },
    { source: { ...input.source, captured_at: `2026-09-11T06:00:00.${'0'.repeat(22)}Z` } },
    { request_id: 'another' },
    { csv_text: 'x'.repeat(8 * 1024 * 1024 + 1) },
    { csv_text: '₹'.repeat(3 * 1024 * 1024) },
  ])('rejects unsupported or oversized capture %#', (changes) => {
    expect(chartinkPayload({ ...input, ...changes }, requestId)).toBeNull()
  })
  it('accepts the shared contract boundaries and preserves the exact CSV text', () => {
    const csv = '\uFEFFDate,Symbol,Note\r\n2026-09-11,INFY,"₹ 100"\r\n\r\n'
    const value = {
      ...input,
      request_id: requestId.toUpperCase(),
      csv_text: csv,
      source: {
        ...input.source,
        url: `https://chartink.com/screener/a_${'b'.repeat(198)}/`,
        title: 'x'.repeat(240),
        selected_period: 'x'.repeat(80),
        captured_at: '2026-09-11T11:30:00+05:30',
      },
    }
    expect(chartinkPayload(value, requestId.toUpperCase())).toEqual(value)
    expect(chartinkPayload(value, requestId.toUpperCase())?.csv_text).toBe(csv)
  })
  it('keeps the source history label, period and safe scanner link compact', () => {
    const { rerender } = render(<ChartinkSource source={input.source} />)
    expect(screen.getByRole('link', { name: 'Chartink history' })).toHaveAttribute(
      'href',
      input.source.url
    )
    expect(screen.getByText('· 3 months')).toBeVisible()
    rerender(<ChartinkSource source={{ ...input.source, url: 'javascript:alert(1)' }} />)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})
