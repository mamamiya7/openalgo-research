import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type AlertChannel, alertsApi } from '@/api/alerts'
import { type JourneyState, researchJourney } from '@/api/researchJourney'
import { ResearchJourney } from './ResearchJourney'

vi.mock('@/api/researchJourney', () => ({
  researchJourney: { prepare: vi.fn(), get: vi.fn(), updatePlan: vi.fn(), start: vi.fn() },
}))
vi.mock('@/api/alerts', () => ({
  alertsApi: { list: vi.fn(), update: vi.fn(), saveWebhook: vi.fn(), sendTest: vi.fn() },
}))

function noticeChannel(name: 'slack' | 'discord', enabled = true, configured = true): AlertChannel {
  return {
    name, label: name === 'slack' ? 'Slack' : 'Discord', setup_path: `/${name}`,
    uses_webhook: true, enabled, categories: [],
    status: { configured, ready: configured, detail: configured ? 'Configured' : 'No webhook' },
  }
}

function state(): JourneyState {
  return {
    version: 'research-journey-v1',
    phase: 'review',
    revision: 1,
    can_start: true,
    preparation: {
      job_id: 'prep',
      status: 'completed',
      progress: 100,
      activity: null,
      ready: true,
      error: null,
    },
    plan: {
      id: 'plan1',
      revision: 1,
      style: 'positional',
      objective: {
        id: 'robust_improvement',
        label: 'Improve results with controlled risk',
        reason: 'Compare supported changes against the original setup.',
      },
      engine: { id: 'vectorbt', label: 'VectorBT', reason: 'Supports these supplied signals.' },
      data: {
        stocks: 5,
        signals: 68,
        from: '2026-03-11',
        to: '2026-10-16',
        excluded: [{ symbol: 'EXCLUDED', reason: 'Required prices unavailable' }],
      },
      settings: {
        capital: 100000,
        order_size_pct: 3,
        hold_sessions: 46,
        stop_pct: 45,
        target_pct: 50,
      },
      tests: ['Higher costs', 'Nearby settings', 'Consistency over time'],
      estimate: { seconds: null, label: 'No matching measured run is available yet.' },
      memory: {
        considered: 2,
        retest_reason: 'New dated signals justify checking earlier findings.',
      },
    },
    run: null,
    decisions: [],
    result: null,
  }
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(alertsApi.list).mockResolvedValue({ categories: [], channels: [] })
  vi.mocked(researchJourney.prepare).mockResolvedValue(state())
  vi.mocked(researchJourney.get).mockResolvedValue(state())
  vi.mocked(researchJourney.updatePlan).mockResolvedValue(state())
  vi.mocked(researchJourney.start).mockResolvedValue({
    ...state(),
    phase: 'queued',
    can_start: false,
  })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('Reviewed automatic research journey', () => {
  it('keeps even configured notifications off by default and omits them from Start', async () => {
    vi.mocked(alertsApi.list).mockResolvedValue({
      categories: [], channels: [noticeChannel('slack'), noticeChannel('discord')],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    const slack = await screen.findByRole('checkbox', { name: 'Slack' })
    const discord = screen.getByRole('checkbox', { name: 'Discord' })
    await waitFor(() => expect(slack).toBeEnabled())
    expect(slack).not.toBeChecked()
    expect(discord).not.toBeChecked()
    expect(screen.getByRole('link', { name: 'Configure channels in Alerts' })).toHaveAttribute('href', '/alerts')
    fireEvent.click(screen.getByRole('button', { name: 'Start research' }))
    await waitFor(() => expect(researchJourney.start).toHaveBeenCalledTimes(1))
    expect(researchJourney.start).toHaveBeenCalledWith('e', expect.not.objectContaining({ notification_channels: expect.anything() }), expect.any(AbortSignal))
    expect(alertsApi.update).not.toHaveBeenCalled()
    expect(alertsApi.saveWebhook).not.toHaveBeenCalled()
    expect(alertsApi.sendTest).not.toHaveBeenCalled()
  })
  it('sends only the channels deliberately selected by the user', async () => {
    vi.mocked(alertsApi.list).mockResolvedValue({
      categories: [], channels: [noticeChannel('slack'), noticeChannel('discord')],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    const slack = await screen.findByRole('checkbox', { name: 'Slack' })
    await waitFor(() => expect(slack).toBeEnabled())
    fireEvent.click(slack)
    expect(slack).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Discord' })).not.toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Start research' }))
    await waitFor(() => expect(researchJourney.start).toHaveBeenCalledWith('e', expect.objectContaining({ notification_channels: ['slack'] }), expect.any(AbortSignal)))
    expect(alertsApi.update).not.toHaveBeenCalled()
    expect(alertsApi.sendTest).not.toHaveBeenCalled()
  })
  it('cannot select disabled or unconfigured channels but can still start research', async () => {
    vi.mocked(alertsApi.list).mockResolvedValue({
      categories: [], channels: [noticeChannel('slack', false), noticeChannel('discord', true, false)],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    const slack = await screen.findByRole('checkbox', { name: /Slack/ })
    expect(slack).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /Discord/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Start research' })).toBeEnabled()
  })
  it('does not carry notification consent into another study', async () => {
    vi.mocked(alertsApi.list).mockResolvedValue({
      categories: [], channels: [noticeChannel('slack')],
    })
    const view = render(<ResearchJourney experimentId="first" revision={1} enabled />)
    const slack = await screen.findByRole('checkbox', { name: 'Slack' })
    await waitFor(() => expect(slack).toBeEnabled())
    fireEvent.click(slack)
    expect(slack).toBeChecked()
    view.rerender(<ResearchJourney experimentId="second" revision={1} enabled />)
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Slack' })).toBeEnabled())
    expect(screen.getByRole('checkbox', { name: 'Slack' })).not.toBeChecked()
    expect(researchJourney.start).not.toHaveBeenCalled()
    expect(alertsApi.update).not.toHaveBeenCalled()
  })
  it('a failed notification readiness request never enables a channel or blocks research', async () => {
    vi.mocked(alertsApi.list).mockRejectedValue(new Error('Offline'))
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await screen.findByText('Configure channels in Alerts')
    expect(screen.getByRole('checkbox', { name: /Slack/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /Discord/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Start research' })).toBeEnabled()
    expect(alertsApi.sendTest).not.toHaveBeenCalled()
  })
  it('shows uncertain delivery honestly without retrying or changing channel settings', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      notification_channels: ['slack'],
      notifications: [{ channel: 'slack', status: 'uncertain', detail: 'The request outcome was not confirmed.' }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(await screen.findByText(/Delivery uncertain; not automatically resent/)).toBeVisible()
    expect(researchJourney.start).not.toHaveBeenCalled()
    expect(alertsApi.update).not.toHaveBeenCalled()
    expect(alertsApi.sendTest).not.toHaveBeenCalled()
  })
  it('reads pending delivery after research completes without rerunning research or sending alerts', async () => {
    vi.useFakeTimers()
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      notifications: [{ channel: 'discord', status: 'watching' }],
    })
    vi.mocked(researchJourney.get).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      notifications: [{ channel: 'discord', status: 'sent' }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await act(async () => {})
    expect(screen.getByText(/Will notify when finished/)).toBeVisible()
    await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
    expect(screen.getByText(/Completion notification sent/)).toBeVisible()
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(researchJourney.get).toHaveBeenCalledTimes(1)
    expect(researchJourney.start).not.toHaveBeenCalled()
    expect(alertsApi.sendTest).not.toHaveBeenCalled()
  })
  it('shows verified native funding values and rejection reasons without claiming acceptance', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      decisions: [{
        remedy: 'funding_and_size', selection: 'native', reason: 'Repair affordability.',
        outcome: 'rejected', reasons: ['Nearby settings did not pass.', 'Nearby settings did not pass.'],
        change: { axis: 'native_funding_and_size', current_capital: 100000 },
        settings_change: { status: 'verified', rows: [
          { strategy: 'Account', field: 'capital', before: 100000, after: 517000 },
          { strategy: 'Stocks', field: 'order_size_pct', before: 3, after: 1.5 },
        ] },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(await screen.findByText('Account · Capital: ₹1,00,000 → ₹5,17,000')).toBeVisible()
    expect(screen.getByText('Stocks · Trade size: 3% → 1.5%')).toBeVisible()
    expect(screen.getByText('Chosen by the native controller')).toBeVisible()
    expect(screen.getAllByText('Nearby settings did not pass.')).toHaveLength(1)
    expect(screen.queryByText(/Proposed:/)).not.toBeInTheDocument()
    expect(screen.queryByText('Best passing result saved')).not.toBeInTheDocument()
  })
  it('labels pending model changes as proposed until the saved native result is verified', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'researching', can_start: false,
      decisions: [{
        remedy: 'temporal_stability', selection: 'model', reason: 'Release capacity earlier.',
        change: { axis: 'hold_sessions', from: 20, to: 10 },
        settings_change: { status: 'proposed', rows: [] },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(await screen.findByText('Proposed: Holding sessions: 20 → 10.')).toBeVisible()
    expect(screen.getByText('Chosen by the configured AI model')).toBeVisible()
    expect(screen.queryByText('Tested settings · verified saved report')).not.toBeInTheDocument()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('shows tested holding, exit and trailing changes from a verified rejected report', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      decisions: [{
        remedy: 'lower_turnover', selection: 'model', reason: 'Reduce cost-sensitive exits.',
        outcome: 'rejected', reasons: ['Nearby settings did not pass.'],
        settings_change: { status: 'verified', rows: [
          { strategy: 'Stocks', field: 'hold_sessions', before: 10, after: 40 },
          { strategy: 'Stocks', field: 'exit_mode', before: 'percent', after: 'atr' },
          { strategy: 'Stocks', field: 'trailing_enabled', before: true, after: false },
        ] },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getByText('Stocks · Holding sessions: 10 → 40')).toBeVisible()
    expect(screen.getByText('Stocks · Exit method: Percentage exits → ATR exits')).toBeVisible()
    expect(screen.getByText('Stocks · Trailing stop: On → Off')).toBeVisible()
    expect(screen.queryByText('Best passing result saved')).not.toBeInTheDocument()
  })
  it('retains no-effect and blocked outcomes without suggesting they passed', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      decisions: [{
        remedy: 'reduce_concentration', selection: 'model', reason: 'Test diversification.',
        outcome: 'rejected', no_effect: { status: 'unchanged' },
        reasons: ['The economic execution was unchanged.'],
        settings_change: { status: 'verified', rows: [] },
      }, {
        remedy: 'funding_and_size', selection: 'native', reason: 'Test funding.', outcome: 'blocked',
        change: { axis: 'native_funding_and_size', current_capital: 100000 },
        settings_change: { status: 'blocked', rows: [] },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (2)'))
    expect(await screen.findByText(/No change; further checks skipped/)).toBeVisible()
    expect(screen.getByText('The economic execution was unchanged.')).toBeVisible()
    expect(screen.getByText('Validation blocked. No verified tested-settings comparison.')).toBeVisible()
    expect(screen.getByText('Proposed: estimate capital and trade size during native checks.')).toBeVisible()
    expect(screen.queryByText('Best passing result saved')).not.toBeInTheDocument()
  })
  it('does not promote unverified saved values to tested settings', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      decisions: [{
        remedy: 'lower_turnover', selection: 'model', reason: 'Test exits.', outcome: 'rejected',
        change: { axis: 'trailing_choice', from: 6.25, to: 9.4 },
        settings_change: { status: 'unavailable', rows: [] },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(await screen.findByText('Proposed: Trailing distance: 6.25% → 9.4%.')).toBeVisible()
    expect(screen.getByText('Saved settings could not be verified. Proposed values are not confirmed results.')).toBeVisible()
    expect(screen.queryByText('Tested settings · verified saved report')).not.toBeInTheDocument()
  })
  it('shows the saved reason research stopped without suggesting validation passed', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(),
      phase: 'none',
      can_start: false,
      result: {
        job_id: 'rejected',
        experiment_id: 'e',
        accepted: false,
        headline: 'None of the tested setups passed validation.',
        stopping_reason: 'Only 10 accounts remain; complete validation requires 14.',
        verification: { matched: true, new_calculations: 0 },
      },
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(
      await screen.findByText(
        'Stopped because: Only 10 accounts remain; complete validation requires 14.'
      )
    ).toBeVisible()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('prepares automatically but never starts calculations before the reviewed Start click', async () => {
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await screen.findByRole('heading', { name: 'Review your research plan' })
    expect(researchJourney.prepare).toHaveBeenCalledWith('e', 1, expect.any(AbortSignal))
    expect(researchJourney.start).not.toHaveBeenCalled()
    expect(screen.getByText('Available data')).toBeInTheDocument()
    expect(screen.getByText('Suggested settings')).toBeInTheDocument()
    expect(screen.getByText('What will be tested')).toBeInTheDocument()
    expect(screen.getByText('Expected time')).toBeInTheDocument()
    expect(screen.getByText('EXCLUDED')).toBeInTheDocument()
    expect(screen.getByText(/Required prices unavailable/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start research' })).toBeEnabled()
  })
  it('waits for the parent draft to be saved before preparing', async () => {
    const view = render(<ResearchJourney experimentId="e" revision={1} enabled={false} />)
    expect(researchJourney.prepare).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Start research' })).toBeDisabled()
    view.rerender(<ResearchJourney experimentId="e" revision={2} enabled />)
    await waitFor(() =>
      expect(researchJourney.prepare).toHaveBeenCalledWith('e', 2, expect.any(AbortSignal))
    )
  })
  it('invalidates Start when a setting changes and sends the edited plan explicitly', async () => {
    const onSaved = vi.fn()
    render(<ResearchJourney experimentId="e" revision={1} enabled onSaved={onSaved} />)
    await screen.findByRole('heading', { name: 'Review your research plan' })
    fireEvent.change(screen.getByLabelText('Estimated capital (₹)'), {
      target: { value: '200000' },
    })
    fireEvent.change(screen.getByLabelText('Trading style'), { target: { value: 'intraday' } })
    expect(screen.getByRole('button', { name: 'Start research' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Save plan changes' }))
    await waitFor(() =>
      expect(researchJourney.updatePlan).toHaveBeenCalledWith(
        'e',
        expect.objectContaining({ capital: 200000, style: 'intraday', revision: 1 }),
        expect.any(AbortSignal)
      )
    )
    expect(researchJourney.start).not.toHaveBeenCalled()
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })
  it('refuses a stale plan even when the response incorrectly advertises readiness', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({ ...state(), revision: 2 })
    render(<ResearchJourney experimentId="e" revision={2} enabled />)
    await screen.findByRole('heading', { name: 'Review your research plan' })
    expect(screen.getByRole('button', { name: 'Start research' })).toBeDisabled()
  })
  it('starts exactly once on deliberate clicks and carries the frozen plan identity', async () => {
    let finish!: (value: JourneyState) => void
    vi.mocked(researchJourney.start).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    const button = await screen.findByRole('button', { name: 'Start research' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    fireEvent.click(button)
    expect(researchJourney.start).toHaveBeenCalledTimes(1)
    expect(researchJourney.start).toHaveBeenCalledWith(
      'e',
      expect.objectContaining({ revision: 1, plan_id: 'plan1', request_id: expect.any(String) }),
      expect.any(AbortSignal)
    )
    await act(async () => finish({ ...state(), phase: 'queued', can_start: false }))
  })
  it('shows action and reason while polling, then aborts only the page requests on leaving', async () => {
    vi.useFakeTimers()
    const running: JourneyState = {
      ...state(),
      phase: 'researching',
      can_start: false,
      run: { job_id: 'job', status: 'running', progress: 42 },
      decisions: [
        { remedy: 'funding_and_size', reason: 'Whole shares do not fit the current ticket.' },
      ],
    }
    vi.mocked(researchJourney.prepare).mockResolvedValue(running)
    vi.mocked(researchJourney.get).mockResolvedValue(running)
    const view = render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await act(async () => {})
    expect(
      screen.getAllByText('Whole shares do not fit the current ticket.').length
    ).toBeGreaterThan(0)
    expect(screen.getByRole('progressbar', { name: 'Research progress' })).toHaveValue(42)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500)
    })
    expect(researchJourney.get).toHaveBeenCalledTimes(1)
    const signal = vi.mocked(researchJourney.prepare).mock.calls[0][2]!
    view.unmount()
    expect(signal.aborted).toBe(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(researchJourney.get).toHaveBeenCalledTimes(1)
  })
  it('opens an accepted saved result once without launching another calculation', async () => {
    const onResult = vi.fn()
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(),
      phase: 'complete',
      can_start: false,
      result: {
        accepted: true,
        job_id: 'accepted',
        experiment_id: 'e',
        headline: 'Saved',
        before: { net_pnl: 50 },
        after: { net_pnl: 70 },
        verification: { matched: true, new_calculations: 0 },
      },
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled onResult={onResult} />)
    await waitFor(() => expect(onResult).toHaveBeenCalledExactlyOnceWith('accepted'))
    expect(
      screen.getByText('Saved result verified on reopen. No new calculations.')
    ).toBeInTheDocument()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('recovers from a temporary progress read failure without restarting research', async () => {
    vi.useFakeTimers()
    const running: JourneyState = {
      ...state(),
      phase: 'researching',
      can_start: false,
      run: { job_id: 'job', status: 'running', progress: 42 },
    }
    vi.mocked(researchJourney.prepare).mockResolvedValue(running)
    vi.mocked(researchJourney.get)
      .mockRejectedValueOnce(new Error('Connection interrupted'))
      .mockResolvedValue(running)
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(researchJourney.get).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(researchJourney.start).not.toHaveBeenCalled()
    expect(researchJourney.prepare).toHaveBeenCalledTimes(1)
  })
  it('finishes no-result honestly and never opens a rejected report as an accepted setup', async () => {
    const onResult = vi.fn()
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(),
      phase: 'none',
      can_start: false,
      result: { accepted: false, job_id: 'rejected', experiment_id: 'e', headline: 'None passed' },
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled onResult={onResult} />)
    await screen.findByRole('heading', { name: 'None of the tested setups passed validation.' })
    expect(onResult).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Start research' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'View tested report' }))
    expect(onResult).toHaveBeenCalledExactlyOnceWith('rejected')
  })
  it('keeps finalization read-only and waits for exact saved verification', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(),
      phase: 'finalizing',
      can_start: false,
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await screen.findByRole('heading', { name: 'Saving and verifying the result' })
    expect(
      screen.getByText('Checking that saved settings and results reopen exactly.')
    ).toBeInTheDocument()
    expect(screen.queryByLabelText('Trading style')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start research' })).not.toBeInTheDocument()
  })
  it('does not call an approved running plan stale when admission increments the study revision', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(),
      phase: 'researching',
      revision: 2,
      can_start: false,
    })
    render(<ResearchJourney experimentId="e" revision={2} enabled />)
    await screen.findByRole('heading', { name: 'Research in progress' })
    expect(
      screen.queryByText('Save your setup changes to refresh this plan before starting.')
    ).not.toBeInTheDocument()
  })
})
