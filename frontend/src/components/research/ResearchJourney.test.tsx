import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type AlertChannel, alertsApi } from '@/api/alerts'
import { type JourneyState, researchJourney } from '@/api/researchJourney'
import { ResearchJourney } from './ResearchJourney'

vi.mock('@/api/researchJourney', () => ({
  researchJourney: { prepare: vi.fn(), get: vi.fn(), updatePlan: vi.fn(), start: vi.fn(), approve: vi.fn() },
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
  it('defaults to all-signal improvement with hands-off control and supported editable permissions', async () => {
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(await screen.findByRole('radio', { name: /Improve trading settings/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Hands off/ })).toBeChecked()
    for (const name of ['Capital', 'Trade size', 'Holding sessions', 'Stop loss', 'Profit target', 'Trailing distance', 'Allocation']) {
      expect(screen.getByRole('checkbox', { name: `${name} · AI may adjust` })).toBeChecked()
    }
    expect(screen.getByText('Selecting a smaller set of stocks is not available in this flow.')).toBeVisible()
    expect(screen.queryByRole('combobox', { name: /stock/i })).not.toBeInTheDocument()
  })
  it('saves guided permissions without changing the full improve budget', async () => {
    const ready = state()
    ready.plan!.budget = { proposals: 408, max_simulations: 512 }
    vi.mocked(researchJourney.prepare).mockResolvedValue(ready)
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await screen.findByRole('radio', { name: /Guided/ })
    fireEvent.click(screen.getByRole('radio', { name: /Guided/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Capital · AI may adjust' }))
    expect(screen.getByRole('checkbox', { name: 'Capital · Keep fixed' })).not.toBeChecked()
    expect(screen.getByText(/408 proposals within 512 total account calculations/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Save plan changes' }))
    await waitFor(() => expect(researchJourney.updatePlan).toHaveBeenCalledWith('e', expect.objectContaining({
      flow: { version: 'research-user-flow-v1', intent: 'improve', involvement: 'guided', adjustable: ['order_size_pct', 'hold_sessions', 'stop_pct', 'target_pct', 'trailing_pct', 'allocation_pct'] },
    }), expect.any(AbortSignal)))
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('visibly switches hands-on to exact replay and requires saving before one exact run', async () => {
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByRole('radio', { name: /Hands on/ }))
    expect(screen.getByRole('radio', { name: 'Replay exact settings' })).toBeChecked()
    expect(screen.getByText('Replay allowance: one exact account calculation. No optimization search.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Run exact backtest' })).toBeDisabled()
    expect(screen.queryByRole('group', { name: 'What may AI adjust?' })).not.toBeInTheDocument()
    expect(screen.queryByText(/AI proposes hypotheses/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save plan changes' }))
    await waitFor(() => expect(researchJourney.updatePlan).toHaveBeenCalledWith('e', expect.objectContaining({
      flow: expect.objectContaining({ intent: 'replay', involvement: 'hands_on' }),
    }), expect.any(AbortSignal)))
  })
  it('keeps unsaved flow choices while the same plan is polled', async () => {
    vi.useFakeTimers()
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('radio', { name: /Guided/ }))
    await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
    expect(researchJourney.get).toHaveBeenCalled()
    expect(screen.getByRole('radio', { name: /Guided/ })).toBeChecked()
    expect(screen.getByRole('button', { name: 'Save plan changes' })).toBeVisible()
  })
  it('shows stored per-strategy values without flattening different allocations', async () => {
    const ready = state()
    ready.plan!.baseline_settings = [
      { id: 'a', name: 'First scan', allocation_pct: 60, config: { hold_sessions: 4, trailing_pct: 1 } },
      { id: 'b', name: 'Second scan', allocation_pct: 40, config: { hold_sessions: 8, trailing_pct: 2 } },
    ]
    vi.mocked(researchJourney.prepare).mockResolvedValue(ready)
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await screen.findByText('Stored settings by strategy')
    fireEvent.click(screen.getByText('Stored settings by strategy'))
    expect(screen.getByText('Allocation: 60%')).toBeVisible()
    expect(screen.getByText('Allocation: 40%')).toBeVisible()
    expect(screen.getByText('Holding sessions: 4')).toBeVisible()
    expect(screen.getByText('Holding sessions: 8')).toBeVisible()
    expect(screen.queryByRole('spinbutton', { name: 'Allocation (%)' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save plan changes' })).not.toBeInTheDocument()
  })
  it('allows an exact single-strategy trailing and allocation edit using its saved values', async () => {
    const ready = state()
    ready.plan!.baseline_settings = [{ id: 'a', allocation_pct: 100, config: { trailing_pct: 0 } }]
    vi.mocked(researchJourney.prepare).mockResolvedValue(ready)
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByRole('radio', { name: /Hands on/ }))
    fireEvent.click(screen.getByText('Holding and exit settings'))
    expect(screen.getByRole('spinbutton', { name: 'Trailing distance (%)' })).toHaveValue(0)
    expect(screen.getByRole('spinbutton', { name: 'Allocation (%)' })).toHaveValue(100)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Trailing distance (%)' }), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save plan changes' }))
    await waitFor(() => expect(researchJourney.updatePlan).toHaveBeenCalledWith('e', expect.objectContaining({ trailing_pct: 2, allocation_pct: 100, flow: expect.objectContaining({ intent: 'replay', involvement: 'hands_on' }) }), expect.any(AbortSignal)))
  })
  it.each(['approve', 'reject'] as const)('submits a guided %s decision once without marking proposed settings accepted', async (decision) => {
    const waiting: JourneyState = {
      ...state(), phase: 'awaiting_approval', can_start: false,
      pending_approval: { id: 'approval1', kind: 'search', job_id: 'job1', reason: 'Test shorter holding after weak development results.', hypothesis: 'Shorter holding may reduce exposure.', domains: { scan: { hold_sessions: { min: 2, max: 6, step: 2 } } } },
    }
    vi.mocked(researchJourney.prepare).mockResolvedValue(waiting)
    vi.mocked(researchJourney.approve).mockResolvedValue({ ...waiting, phase: 'researching', pending_approval: null })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(await screen.findByText('Research is paused for your review')).toBeVisible()
    expect(screen.getByText(/This is a proposal/)).toBeVisible()
    expect(screen.getByText(/Proposed scan · Holding sessions: 2 – 6/)).toBeVisible()
    expect(screen.queryByText(/native validation passed/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start research' })).not.toBeInTheDocument()
    expect(researchJourney.approve).not.toHaveBeenCalled()
    const button = screen.getByRole('button', { name: decision === 'approve' ? 'Approve next step' : 'Stop research' })
    fireEvent.click(button)
    fireEvent.click(button)
    await waitFor(() => expect(researchJourney.approve).toHaveBeenCalledTimes(1))
    expect(researchJourney.approve).toHaveBeenCalledWith('e', { revision: 1, approval_id: 'approval1', decision }, expect.any(AbortSignal))
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('shows the concrete guided continuation settings before asking for approval', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'awaiting_approval', can_start: false,
      pending_approval: {
        id: 'continuation1', kind: 'continuation', job_id: 'job1', domains: {},
        reason: 'Test a smaller ticket and ATR exits after the funding check.',
        settings: [{
          id: 'scan', name: 'Saved scan', allocation_pct: 80,
          config: { initial_capital: 150000, order_size_pct: 2, hold_sessions: 6, stop_pct: 3, target_pct: 7, trailing_enabled: true, trailing_pct: 1.5 },
          execution_policy: { exit_mode: 'atr', sizing: 'stop_risk', allocation_mode: 'original', atr_period: 14, atr_stop_multiple: 2, atr_target_multiple: 4 },
        }],
      },
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(await screen.findByRole('region', { name: 'Proposed next settings' })).toBeVisible()
    for (const value of ['Saved scan', 'Capital: ₹1,50,000', 'Trade size: 2%', 'Holding sessions: 6', 'Stop loss: 3%', 'Profit target: 7%', 'Trailing stop: On', 'Trailing distance: 1.5%', 'Allocation: 80%', 'Exit method: ATR exits', 'Sizing method: Stop-risk sizing', 'ATR period: 14', 'ATR stop multiple: 2', 'ATR target multiple: 4']) {
      expect(screen.getByText(value)).toBeVisible()
    }
    expect(screen.getByText('Proposed settings · not tested or validated yet')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Approve next step' })).toBeEnabled()
    expect(researchJourney.approve).not.toHaveBeenCalled()
    expect(screen.queryByText(/native validation passed/i)).not.toBeInTheDocument()
  })
  it('shows a completed replay as a saved exact backtest and reopens it without an acceptance claim', async () => {
    const onResult = vi.fn()
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'complete', can_start: false,
      result: { job_id: 'replay1', experiment_id: 'e', kind: 'replay', accepted: false, headline: 'Saved backtest', after: { net_pnl: 1234 }, verification: { matched: true, new_calculations: 0 } },
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled onResult={onResult} />)
    expect(await screen.findByRole('heading', { name: 'Saved backtest' })).toBeVisible()
    expect(screen.getByText('Backtest P&L')).toBeVisible()
    expect(screen.getByText(/No optimized setup or validation pass is claimed/)).toBeVisible()
    expect(screen.getByText('Saved result verified on reopen. No new calculations.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'View saved backtest' })).toBeVisible()
    expect(screen.queryByText('None of the tested setups passed validation.')).not.toBeInTheDocument()
    expect(screen.queryByText('Selected setup P&L')).not.toBeInTheDocument()
    await waitFor(() => expect(onResult).toHaveBeenCalledExactlyOnceWith('replay1'))
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('shows a saved user stop honestly without claiming all tested setups failed validation', async () => {
    const onResult = vi.fn()
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      result: {
        job_id: 'stopped1', experiment_id: 'e', kind: 'stopped', accepted: false,
        headline: 'You stopped research', stopping_reason: 'You chose to stop before the next experiment.',
        verification: { matched: true, new_calculations: 0 },
      },
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled onResult={onResult} />)
    expect(await screen.findByRole('heading', { name: 'You stopped research' })).toBeVisible()
    expect(screen.getByText(/Your stop decision and the research completed so far are saved/)).toBeVisible()
    expect(screen.getByText('Stopped because: You chose to stop before the next experiment.')).toBeVisible()
    expect(screen.queryByText('None of the tested setups passed validation.')).not.toBeInTheDocument()
    expect(screen.queryByText('Best passing result saved')).not.toBeInTheDocument()
    expect(onResult).not.toHaveBeenCalled()
    expect(researchJourney.start).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'View tested report' }))
    expect(onResult).toHaveBeenCalledExactlyOnceWith('stopped1')
  })
  it('reviews fixed scan signals and the server-owned full budget before one Start', async () => {
    const ready = state()
    ready.plan!.budget = { proposals: 408, max_simulations: 512 }
    vi.mocked(researchJourney.prepare).mockResolvedValue(ready)
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(await screen.findByText('Keep all scan signals and find better trading settings')).toBeVisible()
    expect(screen.getByText(/408 proposals within 512 total account calculations, including validation/)).toBeVisible()
    expect(screen.getByText(/Original signals, trading style and risk limits stay fixed after Start/)).toBeVisible()
    expect(researchJourney.start).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Start research' }))
    await waitFor(() => expect(researchJourney.start).toHaveBeenCalledTimes(1))
    expect(researchJourney.start).toHaveBeenCalledWith('e', {
      revision: 1, plan_id: 'plan1', request_id: expect.any(String),
    }, expect.any(AbortSignal))
  })
  it('does not invent a search allowance for older saved plans without a budget receipt', async () => {
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    await screen.findByText('Keep all scan signals and find better trading settings')
    expect(screen.queryByText(/Research allowance:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/AI proposes hypotheses and permitted search ranges/)).not.toBeInTheDocument()
  })
  it('explains hands-off AI planning only when the reviewed native contract enables it', async () => {
    const ready = state()
    ready.plan!.contract = { signal_policy: 'fixed_signals', planning_policy: 'hands-off-fixed-signals-v1' }
    vi.mocked(researchJourney.prepare).mockResolvedValue(ready)
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(await screen.findByText(/AI proposes hypotheses and permitted search ranges/)).toBeVisible()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('shows actual AI planning stage without presenting earlier decisions as the current experiment', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'researching', can_start: false,
      run: { job_id: 'job', status: 'running', progress: 40, stage: 'ai_planning' },
      decisions: [{ remedy: 'lower_turnover', selection: 'model', reason: 'An earlier hypothesis.' }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(await screen.findByText('AI planning the next experiment')).toBeVisible()
    expect(screen.getByText(/Reviewing development results and permitted changes/)).toBeVisible()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('shows the actual AI hypothesis and Optuna ranges as planned until native results are verified', async () => {
    const planning = {
      version: 'hands-off-plan-v1' as const, action: 'search' as const,
      method: 'holding_and_exits' as const,
      hypothesis: 'Longer holding may reduce measured transaction costs.',
      reason: 'Higher-cost evidence failed.',
      expected_evidence: 'Compare net return and drawdown after native cost checks.',
      stop_rule: 'Stop if no distinct supported configuration addresses the remaining failures.',
      domains: { scan: { hold_sessions: { min: 5, max: 20, step: 5 } } },
    }
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'researching', can_start: false,
      decisions: [{
        remedy: 'lower_turnover', selection: 'model', reason: planning.reason,
        planning, settings_change: { status: 'proposed', rows: [] },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getByText('Planned search; results not verified yet')).toBeVisible()
    expect(screen.getByText(`Hypothesis: ${planning.hypothesis}`)).toBeVisible()
    fireEvent.click(screen.getByText('Planned Optuna ranges'))
    expect(screen.getByText('scan · Holding sessions: 5 – 20 (step 5)')).toBeVisible()
    expect(screen.getByText(`Expected evidence: ${planning.expected_evidence}`)).toBeVisible()
    expect(screen.queryByText(/native validation passed/)).not.toBeInTheDocument()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('keeps a completed rejected AI search distinct from an accepted setup and shows feedback', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      decisions: [{
        remedy: 'temporal_stability', selection: 'model', reason: 'Test a shorter effective holding range.',
        outcome: 'rejected', reasons: ['The neighboring accounts did not pass.'],
        settings_change: { status: 'verified', rows: [
          { strategy: 'Stocks', field: 'hold_sessions', before: 20, after: 10 },
        ] },
        planning: {
          version: 'hands-off-plan-v1', action: 'search', method: 'joint_trading',
          hypothesis: 'Shorter holding and smaller tickets may reduce occupancy.',
          reason: 'Measured occupancy and stability need investigation.',
          expected_evidence: 'Recheck exact selected settings.', stop_rule: 'Stop if unchanged checks still fail.',
          domains: { scan: { hold_sessions: { min: 5, max: 15, step: 5 } } },
        },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getByText('Tested search; validation did not pass')).toBeVisible()
    expect(screen.getByText('Stocks · Holding sessions: 20 → 10')).toBeVisible()
    expect(screen.getByText('The neighboring accounts did not pass.')).toBeVisible()
    expect(screen.queryByText('Tested search; native validation passed')).not.toBeInTheDocument()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('shows a reasoned AI plan stop without presenting another proposed search', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      decisions: [{
        remedy: 'stop', selection: 'model', reason: 'Holding changes cannot repair insufficient sample evidence.',
        settings_change: { status: 'proposed', rows: [] },
        planning: {
          version: 'hands-off-plan-v1', action: 'stop', method: null,
          hypothesis: '', reason: 'Holding changes cannot repair insufficient sample evidence.',
          expected_evidence: '', stop_rule: 'No justified next experiment remains.', domains: {},
        },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getByText('AI stopped research')).toBeVisible()
    expect(screen.getByText('Holding changes cannot repair insufficient sample evidence.')).toBeVisible()
    expect(screen.queryByText('Planned Optuna ranges')).not.toBeInTheDocument()
    expect(screen.queryByText('Optuna ran within the planned ranges. Exact selected settings are not verified here.')).not.toBeInTheDocument()
    expect(screen.queryByText('Tested settings · verified saved report')).not.toBeInTheDocument()
    expect(screen.queryByText('Best passing result saved')).not.toBeInTheDocument()
  })
  it('shows an explicit defer as continuing the approved search without claiming new settings ran', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'researching', can_start: false,
      run: { job_id: 'job', status: 'running', progress: 40, stage: 'optimization' },
      decisions: [{
        remedy: 'defer', selection: 'model', reason: 'This prefix does not justify a new range yet.',
        planning: {
          version: 'hands-off-plan-v1', action: 'defer', method: null,
          hypothesis: 'Continue gathering the approved development evidence.',
          reason: 'This prefix does not justify a new range yet.',
          expected_evidence: 'The full approved search will supply more measured accounts.',
          stop_rule: 'Reassess the next frozen development boundary.', domains: {},
        },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getByText('Continue approved search')).toBeVisible()
    expect(screen.getAllByText('This prefix does not justify a new range yet.')[0]).toBeVisible()
    expect(screen.getByText('No new AI parameter ranges were applied. The existing approved Optuna programme continues.')).toBeVisible()
    expect(screen.queryByText('Planned Optuna ranges')).not.toBeInTheDocument()
    expect(screen.queryByText('AI stopped research')).not.toBeInTheDocument()
    expect(screen.queryByText('Tested settings · verified saved report')).not.toBeInTheDocument()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('does not call a searched range validated before exact native result verification', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'researching', can_start: false,
      decisions: [{
        remedy: 'lower_turnover', selection: 'model', reason: 'Read actual search feedback.',
        outcome: 'validated', settings_change: { status: 'tested', rows: [] },
        planning: {
          version: 'hands-off-plan-v1', action: 'search', method: 'holding_and_exits',
          hypothesis: 'Test a distinct holding region.', reason: 'Read actual search feedback.',
          expected_evidence: 'Native account checks.', stop_rule: 'Stop if supported ideas are exhausted.',
          domains: { scan: { hold_sessions: { min: 5, max: 15, step: 5 } } },
        },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getByText('Search executed; acceptance not confirmed')).toBeVisible()
    expect(screen.getByText('Optuna ran within the planned ranges. Exact selected settings are not verified here.')).toBeVisible()
    expect(screen.queryByText('Tested search; native validation passed')).not.toBeInTheDocument()
    expect(screen.queryByText('Tested settings · verified saved report')).not.toBeInTheDocument()
  })
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
  it('shows the tested hypothesis and what would disconfirm it in saved decisions', async () => {
    const prediction = 'The shorter holding period should improve support from two of four neighbors to at least three.'
    const falsifier = 'If neighboring support remains below three of four, reject this temporal-stability hypothesis.'
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      decisions: [{
        remedy: 'temporal_stability', selection: 'model', outcome: 'rejected',
        reason: 'Test the recorded failed neighboring-settings check.',
        diagnosis: {
          target_ref: 'b1/neighboring_settings', cause: 'temporal_instability', prediction, falsifier,
          version: 'research-failure-diagnosis-v1', context_key: 'sealed-context',
          candidate_id: 'candidate', origin_job_id: 'checked-source',
        },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getByRole('region', { name: 'Testable hypothesis' })).toBeVisible()
    expect(screen.getByText(`Expected change: ${prediction}`)).toBeVisible()
    expect(screen.getByText(`Would disconfirm: ${falsifier}`)).toBeVisible()
    expect(researchJourney.start).not.toHaveBeenCalled()
    expect(screen.queryByText('Best passing result saved')).not.toBeInTheDocument()
  })
  it('distinguishes a repeated failed gate from unchanged origin execution', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      decisions: [{
        remedy: 'lower_turnover', selection: 'model', outcome: 'rejected',
        reason: 'The charged probes repeated a failed cost comparison from another checked setup.',
        no_effect: { status: 'unchanged', kind: 'repeated_failed_gate' },
        change: { axis: 'trailing_choice', from: 3.12, to: 10.97 },
        settings_change: { status: 'verified', rows: [{ strategy: 'Stocks', field: 'trailing_pct', before: 3.12, after: 10.97 }] },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getByText(/Repeated failed check; remaining checks skipped/)).toBeVisible()
    expect(screen.queryByText(/No change; further checks skipped/)).not.toBeInTheDocument()
    expect(screen.getByText('Stocks · Trailing distance: 3.12% → 10.97%')).toBeVisible()
    expect(screen.queryByText('Best passing result saved')).not.toBeInTheDocument()
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
  it('distinguishes a reviewed model stop from the call allowance also reaching its limit', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      result: {
        job_id: 'reviewed', experiment_id: 'e', accepted: false,
        headline: 'None of the tested setups passed validation.',
        stopping: {
          kind: 'model_stop', decision_source: 'model', reason: 'Each supported alternative lacks a distinct justified mechanism.',
          model_budget_exhausted: true, model_calls_spent: 11, model_call_limit: 11,
          native_accounts_remaining: 36, option_count: 2, unreviewed_option_ids: [],
          stop_review: {
            option_ids: ['first-complete-config', 'second-complete-config'], complete: true, unreviewed_options: [],
            reviews: [
              { option: 1, evidence_ref: 'b2/neighboring_settings', reason: 'This holding configuration lacks neighboring support after the recorded shorter-horizon failure.' },
              { option: 2, evidence_ref: 'o2/feasibility', reason: 'Its funding repair changes affordability but supplies no new supported exit mechanism.' },
            ],
          },
        },
      },
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(await screen.findByText('ChatGPT decided to stop')).toBeVisible()
    expect(screen.getByText(/allowance for this stage is also fully used \(11\/11\)/)).toBeVisible()
    expect(screen.queryByText('AI call limit reached')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Why each option was skipped (2)'))
    expect(screen.getByText('This holding configuration lacks neighboring support after the recorded shorter-horizon failure.')).toBeVisible()
    expect(screen.getByText('Its funding repair changes affordability but supplies no new supported exit mechanism.')).toBeVisible()
    expect(screen.getByTitle('first-complete-config')).toHaveTextContent('first-comple')
    expect(screen.queryByText(/were not ruled out/)).not.toBeInTheDocument()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('reports budget exhaustion with unreviewed options without attributing a stop decision to ChatGPT', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      result: {
        job_id: 'limited', experiment_id: 'e', accepted: false,
        headline: 'None of the tested setups passed validation.',
        stopping: {
          kind: 'model_budget_exhausted', decision_source: 'controller', reason: 'The frozen model-call allowance is exhausted.',
          model_budget_exhausted: true, model_calls_spent: 2, model_call_limit: 2,
          native_accounts_remaining: 36, option_count: 3, unreviewed_option_ids: ['second', 'third'],
          stop_review: { option_ids: ['first', 'second', 'third'], complete: false, unreviewed_options: [2, 3], reviews: [
            { option: 1, evidence_ref: 'b1/higher_costs', reason: 'The first complete configuration lacks a separate supported mechanism.' },
          ] },
        },
      },
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    expect(await screen.findByText('AI call limit reached')).toBeVisible()
    expect(screen.getByText('2 remaining options have no completed skip review. They were not ruled out.')).toBeVisible()
    expect(screen.queryByText('ChatGPT decided to stop')).not.toBeInTheDocument()
    expect(screen.getByText('Why each option was skipped (1)')).toBeVisible()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('shows partial stop opinions as reviewing options rather than finished research', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'researching', can_start: false,
      decisions: [{ remedy: 'stop', selection: 'model', reason: 'Review the remaining complete configurations before deciding.',
        stop_review: { option_ids: ['first', 'second'], complete: false, unreviewed_options: [2], reviews: [
          { option: 1, evidence_ref: 'b1/neighboring_settings', reason: 'The first configuration has no distinct supported risk mechanism.' },
        ] },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getAllByText('Reviewing remaining options')[0]).toBeVisible()
    expect(screen.queryByText('Finish research')).not.toBeInTheDocument()
    expect(screen.queryByText('AI stopped research')).not.toBeInTheDocument()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('shows a rejected stop intent without presenting it as an accepted AI stop', async () => {
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'needs_action', can_start: false,
      decisions: [{
        remedy: 'stop', selection: 'model', reason: 'These settings do not offer a useful next test.',
        stop_review_rejected: {
          evidence_id: 'saved-evidence', source_result_artifact: 'saved-artifact',
          reason: 'The stop omitted remaining option reviews.',
        },
      }],
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled />)
    fireEvent.click(await screen.findByText('Research decisions (1)'))
    expect(screen.getAllByText('Stop review rejected')[0]).toBeVisible()
    expect(screen.queryByText('Finish research')).not.toBeInTheDocument()
    expect(screen.queryByText('AI stopped research')).not.toBeInTheDocument()
    expect(screen.queryByText('ChatGPT decided to stop')).not.toBeInTheDocument()
    expect(researchJourney.start).not.toHaveBeenCalled()
  })
  it('shows exact rejected-result reopening without presenting it as a validated recommendation', async () => {
    const onResult = vi.fn()
    vi.mocked(researchJourney.prepare).mockResolvedValue({
      ...state(), phase: 'none', can_start: false,
      result: {
        job_id: 'rejected', experiment_id: 'e', accepted: false,
        headline: 'None of the tested setups passed validation.',
        stopping_reason: 'The permitted alternatives cannot repair the remaining evidence failures.',
        verification: { matched: true, new_calculations: 0 },
      },
    })
    render(<ResearchJourney experimentId="e" revision={1} enabled onResult={onResult} />)
    expect(await screen.findByText('Saved result verified on reopen. No new calculations.')).toBeVisible()
    expect(screen.queryByText('Best passing result saved')).not.toBeInTheDocument()
    expect(onResult).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'View tested report' }))
    expect(onResult).toHaveBeenCalledExactlyOnceWith('rejected')
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
      screen.getByText('Verifying the chosen settings, then saving and reopening the exact result.')
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
