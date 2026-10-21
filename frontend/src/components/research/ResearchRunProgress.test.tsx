import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PortfolioRequest } from '@/api/portfolioResearch'
import type { ResearchActivity, ResearchFailureReceipt } from '@/api/scannerResearch'
import { researchDefaults } from '@/lib/researchDraft'
import {
  ResearchRunProgress,
  ResearchUploadProgress,
  researchRecoveryActions,
} from './ResearchRunProgress'

const base: ResearchActivity = {
  version: 1,
  stage: 'cache',
  started_at: 1788998400,
  updated_at: 1788998430,
  inputs: { files: 2, signals: 420, symbols: 18, excluded_rows: 3 },
  prices: {
    interval: 'D',
    total_symbols: 18,
    checked_symbols: 9,
    covered_symbols: 5,
    required_candles: 1500,
    cached_candles: 600,
    downloaded_candles: 0,
    available_candles: 600,
    missing_candles: 900,
    cache_complete: false,
    current_symbol: 'INFY',
    pending_windows: 10,
  },
}
const job = (activity?: ResearchActivity, status = 'running') => ({
  status,
  resumable: status === 'paused',
  kind: 'portfolio_optimize',
  activity,
})
const portfolio: PortfolioRequest = {
  version: 'research-portfolio-v1',
  name: 'Signals',
  capital: 100000,
  engine: 'vectorbt',
  strategies: [
    {
      id: 'one',
      name: 'Signals',
      type: 'signals',
      source_id: 'csv',
      allocation_pct: 100,
      config: { ...researchDefaults, hold_sessions: 5 },
      search: { hold_sessions: { min: 2, max: 10, step: 2 } },
    },
  ],
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

const hoursFailure: ResearchFailureReceipt = {
  version: 'research-failure-v1',
  code: 'execution_session_hours_mismatch',
  stage: 'prepare_prices',
  retryable: false,
  actions: ['new_run', 'edit_setup'],
  message: 'A real hours mismatch',
  details: {
    first_affected_session: '2026-01-22',
    expected_hours: { open: '09:15', close: '15:30' },
    actual_hours: null,
    mismatch_count: 1,
  },
}

describe('Research progress journey', () => {
  it('does not tell a paused run to Resume when its checkpoint has become incompatible', () => {
    render(
      <ResearchRunProgress
        job={{
          ...job(base, 'paused'),
          resumable: false,
          failure: hoursFailure,
          recovery: {
            version: 'research-recovery-v1',
            status: 'blocked',
            actions: ['new_run'],
            message: 'Start a new run.',
          },
        }}
      />
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Session hours differ')
    expect(
      screen.queryByText('Progress saved. Resume whenever you’re ready.')
    ).not.toBeInTheDocument()
  })

  it('labels native scanner and market-index feature counts explicitly, without inferring roles from legacy totals', () => {
    const { rerender } = render(
      <ResearchRunProgress
        job={job({
          ...base,
          stage: 'features',
          features: {
            total_symbols: 144,
            completed_symbols: 10,
            scanner_symbols: 143,
            context_symbols: 1,
          },
        })}
      />
    )
    expect(screen.getByText('143 scanner symbols + 1 market index')).toBeVisible()
    expect(screen.getByText(/^10 of 144 symbols/)).toBeVisible()
    rerender(
      <ResearchRunProgress
        job={job({
          ...base,
          stage: 'features',
          features: { total_symbols: 144, completed_symbols: 10 },
        })}
      />
    )
    expect(screen.queryByText(/scanner symbols|market index/)).not.toBeInTheDocument()
    expect(screen.getByText(/^10 of 144 symbols/)).toBeVisible()
  })

  it('admits recovery actions from known server capabilities and suppresses contradictory Resume flags', () => {
    expect(
      researchRecoveryActions({ status: 'failed', failure: hoursFailure, resumable: true })
    ).toEqual(['new_run', 'edit_setup'])
    expect(
      researchRecoveryActions({
        status: 'failed',
        resumable: true,
        recovery: {
          version: 'research-recovery-v1',
          status: 'blocked',
          actions: ['resume', 'new_run'],
          message: 'Cannot resume',
        },
      })
    ).toEqual(['new_run'])
    expect(
      researchRecoveryActions({
        status: 'paused',
        resumable: true,
        recovery: {
          version: 'research-recovery-v1',
          status: 'available',
          actions: ['resume'],
          message: 'Saved',
        },
      })
    ).toEqual(['resume'])
    expect(researchRecoveryActions({ status: 'running', resumable: true })).toEqual([])
    expect(
      researchRecoveryActions({
        status: 'paused',
        resumable: false,
        recovery: {
          version: 'research-recovery-v1',
          status: 'available',
          actions: ['resume'],
          message: 'Conflicting flags',
        },
      })
    ).toEqual([])
    expect(
      researchRecoveryActions({
        status: 'failed',
        resumable: true,
        recovery: {
          version: 'future-recovery',
          status: 'available',
          actions: ['resume'],
          message: 'Unknown version',
        } as never,
      })
    ).toEqual([])
  })

  it('keeps an absent recorded session distinct from actual execution hours in issue details', () => {
    render(<ResearchRunProgress job={{ ...job(base, 'failed'), failure: hoursFailure }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Session hours differ on 22 Jan 2026')
    fireEvent.click(screen.getByText('Run issue details'))
    expect(screen.getByText('Expected · saved context').nextElementSibling).toHaveTextContent(
      '09:15–15:30 IST'
    )
    expect(screen.getByText('Actual · execution calendar').nextElementSibling).toHaveTextContent(
      'Not recorded'
    )
  })

  it('separates automatic Search settings, Check and Results without calling a custom search validated', () => {
    const request = {
      ...portfolio,
      automatic_research: { version: 'automatic-hypotheses-v7' as const },
    }
    const { rerender } = render(
      <ResearchRunProgress
        job={{ ...job({ ...base, stage: 'validation' }), specification: { portfolio: request } }}
      />
    )
    const stages = screen.getByRole('list', { name: 'Run stages' })
    expect(stages).toHaveAttribute('data-steps', '5')
    expect(within(stages).getByText('Search settings').closest('li')).toHaveAttribute(
      'data-state',
      'complete'
    )
    expect(within(stages).getByText('Check').closest('li')).toHaveAttribute('aria-current', 'step')
    expect(within(stages).getByText('Results').closest('li')).toHaveAttribute(
      'data-state',
      'waiting'
    )
    rerender(
      <ResearchRunProgress
        job={{ ...job({ ...base, stage: 'optimizing' }), specification: { portfolio } }}
      />
    )
    expect(screen.queryByText('Check', { exact: true })).not.toBeInTheDocument()
    expect(stages).toHaveAttribute('data-steps', '4')
  })

  it('distinguishes chart generation from financial validation', () => {
    render(<ResearchRunProgress job={job({ ...base, stage: 'analysis' })} />)
    expect(screen.getByRole('status')).toHaveTextContent('Preparing study charts')
    expect(screen.getByRole('progressbar')).toHaveAccessibleName('Preparing study charts')
    expect(screen.getByRole('status')).not.toHaveTextContent('Checking')
  })
  it('shows actual calculation counts during automatic checks without treating the budget as an exact total', () => {
    const automatic = {
      ...portfolio,
      automatic_research: { version: 'automatic-hypotheses-v5' as const },
    }
    const { rerender } = render(
      <ResearchRunProgress
        job={{
          ...job({ ...base, stage: 'validation', calculations: { completed: 160, limit: 320 } }),
          specification: { portfolio: automatic },
        }}
      />
    )
    expect(screen.getByRole('status')).toHaveTextContent('Checking shortlisted settings')
    expect(screen.getByText('160 calculations completed · limit 320')).toBeVisible()
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.getByRole('status')).not.toHaveTextContent('160')
    rerender(
      <ResearchRunProgress
        job={{
          ...job({ ...base, stage: 'validation', calculations: { completed: 180, limit: 320 } }),
          specification: { portfolio: automatic },
        }}
      />
    )
    expect(screen.getByText('180 calculations completed · limit 320')).toBeVisible()
    rerender(<ResearchRunProgress job={job({ ...base, stage: 'validation' })} />)
    expect(screen.getByRole('status')).toHaveTextContent('Checking the later period')
    expect(screen.queryByText(/calculations completed/)).not.toBeInTheDocument()
  })
  it('shows the exact planned candles before archive checks finish, without claiming download totals', () => {
    render(<ResearchRunProgress job={job(base)} />)
    expect(screen.getByText('420 signals · 18 symbols')).toBeVisible()
    expect(screen.getByText('Daily')).toBeVisible()
    expect(screen.getByText('9 of 18 symbols checked')).toBeVisible()
    expect(screen.getByRole('progressbar', { name: 'Required candles available' })).toHaveAttribute(
      'value',
      '600'
    )
    expect(screen.getByRole('progressbar')).toHaveAttribute('max', '1500')
    expect(screen.queryByText('Still needed')).not.toBeInTheDocument()
    expect(screen.queryByText(/trials completed/)).not.toBeInTheDocument()
    expect(screen.getByText('/ 1,500')).toBeVisible()
  })

  it.each([
    ['2026-01-22', '2026-08-08', '22 Jan–8 Aug 2026'],
    ['2025-12-22', '2026-01-08', '22 Dec 2025–8 Jan 2026'],
    ['2026-01-22', '2026-01-22', '22 Jan 2026'],
  ])('shows the saved signal dates %s through %s under Read CSV', (date_from, date_to, label) => {
    render(<ResearchRunProgress job={{ ...job(base), source_summary: { date_from, date_to } }} />)
    const csv = within(screen.getByRole('list', { name: 'Run stages' })).getAllByRole('listitem')[0]
    expect(within(csv).getByText(label)).toBeVisible()
    expect(within(csv).getByText('420 signals · 18 symbols')).toBeVisible()
  })

  it.each([
    ['2026-02-30', '2026-03-02'],
    ['2026-02-03', '2026-02-02'],
    ['2026-02-03', undefined],
    ['invalid', '2026-03-02'],
  ])('omits incomplete or invalid saved dates: %s through %s', (date_from, date_to) => {
    render(<ResearchRunProgress job={{ ...job(base), source_summary: { date_from, date_to } }} />)
    const csv = within(screen.getByRole('list', { name: 'Run stages' })).getAllByRole('listitem')[0]
    expect(csv).toHaveTextContent(/^Read CSV420 signals · 18 symbols$/)
  })

  it('explains daily holding windows including entry day, and uses the largest Optuna search window', () => {
    const { rerender } = render(
      <ResearchRunProgress job={{ ...job(base), specification: { portfolio } }} />
    )
    expect(
      screen.getByText('Up to 6 trading days per signal · shared candles counted once')
    ).toBeVisible()
    rerender(
      <ResearchRunProgress
        job={{
          ...job(base),
          specification: {
            portfolio: {
              ...portfolio,
              optimization: { sampler: 'tpe', trials: 10, objective: 'balanced', seed: 42 },
              strategies: [
                ...portfolio.strategies,
                {
                  ...portfolio.strategies[0],
                  id: 'two',
                  config: { ...researchDefaults, hold_sessions: 15 },
                  search: {},
                },
              ],
            },
          },
        }}
      />
    )
    expect(
      screen.getByText('Up to 16 trading days per signal · shared candles counted once')
    ).toBeVisible()
    // The real plan count stays authoritative, rather than signals multiplied by a maximum window.
    expect(screen.getByRole('progressbar')).toHaveAttribute('max', '1500')
    rerender(
      <ResearchRunProgress
        job={{
          ...job(base),
          specification: {
            portfolio: {
              ...portfolio,
              optimization: { sampler: 'tpe', trials: 10, objective: 'balanced', seed: 42 },
            },
          },
        }}
      />
    )
    expect(
      screen.getByText('Up to 11 trading days per signal · shared candles counted once')
    ).toBeVisible()
  })

  it('uses minute entry-to-exit windows without inventing a daily duration', () => {
    render(
      <ResearchRunProgress
        job={{
          ...job({ ...base, prices: { ...base.prices, interval: '1m' } }),
          specification: { portfolio },
        }}
      />
    )
    expect(screen.getByText('Entry-to-exit prices · shared candles counted once')).toBeVisible()
    expect(screen.queryByText(/trading days per signal/)).not.toBeInTheDocument()
  })

  it('waits for a price plan before showing a denominator', () => {
    render(
      <ResearchRunProgress
        job={job({
          ...base,
          prices: {
            ...base.prices,
            required_candles: undefined,
          },
        })}
      />
    )
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.queryByText('/ 1,500')).not.toBeInTheDocument()
  })

  it('shows planned candles even before an available count arrives', () => {
    render(
      <ResearchRunProgress
        job={job({
          ...base,
          stage: 'planning',
          prices: {
            interval: 'D',
            required_candles: 1500,
          },
        })}
      />
    )
    expect(screen.getByText('1,500 candles needed')).toBeVisible()
    expect(screen.queryByText('candles available')).not.toBeInTheDocument()
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
  })

  it('updates verified download counts, and moves to real trials after prices are prepared', () => {
    const download: ResearchActivity = {
      ...base,
      stage: 'download',
      prices: {
        ...base.prices,
        cache_complete: true,
        downloaded_candles: 250,
        available_candles: 850,
        missing_candles: 650,
      },
    }
    const view = render(<ResearchRunProgress job={job(download)} />)
    expect(screen.getByRole('status')).toHaveTextContent('Downloading daily prices')
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '850')
    expect(screen.getByRole('progressbar')).toHaveAttribute('max', '1500')
    expect(screen.getByText('Downloaded').nextElementSibling).toHaveTextContent('250')
    expect(screen.getByText('Already in OpenAlgo').nextElementSibling).toHaveTextContent('600')
    expect(screen.getByText('Still needed').nextElementSibling).toHaveTextContent('650')
    const trials: ResearchActivity = {
      ...download,
      stage: 'optimizing',
      prices: { ...download.prices, available_candles: 1500, missing_candles: 0 },
      trials: {
        total: 10,
        completed: 3,
        active_trial: 4,
        evaluated: 2,
        reused: 1,
        rejected: 0,
        failed: 0,
        history: [
          { trial: 1, score: 0.5 },
          { trial: 3, score: 0.7 },
        ],
      },
    }
    view.rerender(<ResearchRunProgress job={job(trials)} />)
    expect(screen.getByRole('status')).toHaveTextContent('Testing parameters with Optuna')
    expect(screen.getByText('1,500 daily candles')).toBeVisible()
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '3')
    expect(screen.getByRole('progressbar')).toHaveAttribute('max', '10')
    expect(screen.getByText('Testing trial 4')).toBeVisible()
    expect(screen.getByRole('img')).toHaveAccessibleName('Trial score history; latest score 0.700')
    fireEvent.click(screen.getByText('Run details'))
    expect(screen.getByText('Unique calculations').nextElementSibling).toHaveTextContent('2')
    expect(screen.getByText('Reused trials').nextElementSibling).toHaveTextContent('1')
  })

  it('shows the actual trial budget during engine preparation without invented within-trial progress', () => {
    render(
      <ResearchRunProgress
        job={job({
          ...base,
          stage: 'initializing',
          trials: { total: 10, completed: 0, active_trial: 1 },
        })}
      />
    )
    expect(screen.getByText('Preparing trial 1')).toBeVisible()
    expect(screen.getByText('/ 10 trials completed')).toBeVisible()
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('labels a stopped trial as processed rather than a completed calculation', () => {
    render(
      <ResearchRunProgress
        job={job(
          {
            ...base,
            stage: 'optimizing',
            trials: { total: 40, completed: 1, failed: 1, evaluated: 0 },
          },
          'failed'
        )}
      />
    )
    expect(screen.getByText('/ 40 trials processed')).toBeVisible()
    expect(screen.queryByText(/trials completed/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Run details'))
    expect(screen.getByText('Failed trials').nextElementSibling).toHaveTextContent('1')
  })

  it('shows minute prices and broker omissions without counting checked symbols as covered', () => {
    render(
      <ResearchRunProgress
        job={job({
          ...base,
          stage: 'download',
          prices: { ...base.prices, interval: '1m', cache_complete: true, unavailable_candles: 25 },
        })}
      />
    )
    expect(screen.getByText('1-minute')).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent('Downloading 1-minute prices')
    expect(screen.getByText('25 required candles unavailable from the broker')).toBeVisible()
    fireEvent.click(screen.getByText('Run details'))
    expect(screen.getByText('Symbols fully covered').nextElementSibling).toHaveTextContent('5')
  })

  it.each([
    'initializing',
    'backtest',
    'validation',
    'saving',
  ] as const)('keeps %s indeterminate instead of claiming 99 percent', (stage) => {
    render(<ResearchRunProgress job={job({ ...base, stage })} />)
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.queryByText(/99%/)).not.toBeInTheDocument()
    expect(screen.queryByText('Testing parameters with Optuna')).not.toBeInTheDocument()
  })

  it.each([
    ['queued', 'Waiting to continue'],
    ['cancel_requested', 'Stopping your run…'],
    ['cancelling', 'Stopping your run…'],
    ['failed', 'Run stopped'],
    ['interrupted', 'Run interrupted'],
    ['cancelled', 'Run cancelled'],
    ['paused', 'Study paused'],
  ])('overrides stale activity with %s and stops the active animation', (status, title) => {
    const { container } = render(
      <ResearchRunProgress job={job({ ...base, stage: 'download', batch_count: 2 }, status)} />
    )
    expect(screen.getByRole('status')).toHaveTextContent(title)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText('· INFY')).not.toBeInTheDocument()
    expect(container.firstChild).toHaveAttribute('data-running', 'false')
    if (status === 'queued')
      expect(screen.getByText('Saved progress will continue automatically.')).toBeVisible()
  })

  it('keeps real work animated while pausing, then freezes saved counts and history', () => {
    const activity = {
      ...base,
      stage: 'optimizing' as const,
      trials: { total: 25, completed: 3, active_trial: 4, history: [{ trial: 3, score: 0.5 }] },
    }
    const { container, rerender } = render(<ResearchRunProgress job={job(activity, 'pausing')} />)
    expect(screen.getByRole('status')).toHaveTextContent('Saving progress before pausing…')
    expect(screen.getByText('Finishing the current step and saving your trials.')).toBeVisible()
    expect(container.firstChild).toHaveAttribute('data-running', 'true')
    expect(screen.getByText('Testing trial 4')).toBeVisible()
    rerender(
      <ResearchRunProgress
        job={job(
          { ...activity, trials: { ...activity.trials, completed: 4, active_trial: null } },
          'paused'
        )}
      />
    )
    expect(container.firstChild).toHaveAttribute('data-running', 'false')
    expect(screen.getByText('Progress saved. Resume whenever you’re ready.')).toBeVisible()
    expect(screen.queryByText(/Testing trial/)).not.toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.getByRole('img')).toHaveAccessibleName('Trial score history; latest score 0.500')
  })

  it('waits for durable completed status before marking all stages complete', () => {
    const { rerender } = render(<ResearchRunProgress job={job({ ...base, stage: 'saving' })} />)
    let steps = within(screen.getByRole('list', { name: 'Run stages' })).getAllByRole('listitem')
    expect(steps[3]).toHaveAttribute('aria-current', 'step')
    expect(steps[3]).not.toHaveAttribute('data-state', 'complete')
    rerender(<ResearchRunProgress job={job({ ...base, stage: 'complete' }, 'completed')} />)
    steps = within(screen.getByRole('list', { name: 'Run stages' })).getAllByRole('listitem')
    expect(steps.every((step) => step.dataset.state === 'complete')).toBe(true)
    expect(screen.getByRole('status')).toHaveTextContent('Results ready')
  })

  it('reopens old jobs with unknown counts and no invented overall progress', () => {
    render(<ResearchRunProgress job={job()} />)
    expect(screen.getByRole('status')).toHaveTextContent('Running your optimization')
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(
      screen.queryByText(/candles available|trials completed|signals ·/)
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByText('Run details')).not.toBeInTheDocument()
  })

  it('reuses persisted elapsed time, never advances counters on a timer and clears the timer', () => {
    vi.useFakeTimers()
    vi.setSystemTime((base.started_at + 30) * 1000)
    const clear = vi.spyOn(window, 'clearInterval')
    const { rerender, unmount } = render(<ResearchRunProgress job={job(base)} />)
    expect(screen.getByText('30s elapsed')).toBeVisible()
    act(() => vi.advanceTimersByTime(5000))
    expect(screen.getByText('35s elapsed')).toBeVisible()
    expect(screen.getByText('Downloaded').nextElementSibling).toHaveTextContent('0')
    rerender(
      <ResearchRunProgress job={job({ ...base, updated_at: base.started_at + 35 }, 'cancelled')} />
    )
    expect(clear).toHaveBeenCalledTimes(1)
    act(() => vi.advanceTimersByTime(5000))
    expect(screen.getByText('35s elapsed')).toBeVisible()
    rerender(<ResearchRunProgress job={job(base)} />)
    unmount()
    expect(clear).toHaveBeenCalledTimes(2)
  })

  it('announces the stage separately from changing counters and has no accessibility violations', async () => {
    const { container, rerender } = render(<ResearchRunProgress job={job(base)} />)
    expect(screen.getByRole('status')).toHaveTextContent('Checking prices in OpenAlgo')
    expect(screen.getByRole('status')).not.toHaveTextContent('600')
    rerender(
      <ResearchRunProgress
        job={job({ ...base, prices: { ...base.prices, available_candles: 620 } })}
      />
    )
    expect(screen.getByRole('status')).not.toHaveTextContent('620')
    expect((await axe(container)).violations).toEqual([])
  })

  it('bounds real trial history and does not create a chart before trial results exist', () => {
    const { rerender } = render(
      <ResearchRunProgress
        job={job({
          ...base,
          stage: 'optimizing',
          trials: { total: 500, completed: 0, history: [] },
        })}
      />
    )
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    const history = Array.from({ length: 120 }, (_, index) => ({
      trial: index + 1,
      score: index / 100,
    }))
    rerender(
      <ResearchRunProgress
        job={job({ ...base, stage: 'optimizing', trials: { total: 500, completed: 120, history } })}
      />
    )
    expect(screen.getByRole('img')).toHaveAccessibleName('Trial score history; latest score 1.190')
  })

  it('reports completed CSV requests while keeping the current file indeterminate', () => {
    const { rerender } = render(
      <ResearchUploadProgress
        activity={{ total: 2, completed: 0, filename: 'Breakout.csv', signals: 0 }}
      />
    )
    expect(screen.getByRole('status')).toHaveTextContent('Reading CSV 1 of 2')
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.queryByText(/signals accepted/)).not.toBeInTheDocument()
    rerender(
      <ResearchUploadProgress
        activity={{ total: 2, completed: 1, filename: 'Momentum.csv', signals: 24 }}
      />
    )
    expect(screen.getByRole('status')).toHaveTextContent('Reading CSV 2 of 2')
    expect(screen.getByText('1 file processed · 24 signals accepted')).toBeVisible()
    expect(screen.getByText('Momentum.csv')).toBeVisible()
  })
})

it.each([
  ['automatic-hypotheses-v4', 'Preparing stock indicators'],
  ['automatic-hypotheses-v5', 'Preparing stock and market conditions'],
  ['automatic-hypotheses-v6', 'Preparing stock and market conditions'],
  ['automatic-hypotheses-v7', 'Preparing stock and market conditions'],
  ['automatic-hypotheses-v8', 'Preparing stock and market conditions'],
  ['automatic-hypotheses-v14', 'Preparing stock and market conditions'],
  ['automatic-entry-conditions-v1', 'Preparing market conditions'],
] as const)('reports %s warmup separately from completed execution-price counts', (version, title) => {
  render(
    <ResearchRunProgress
      job={{
        ...job({
          ...base,
          stage: 'features',
          features: { completed_symbols: 4, total_symbols: 18 },
        }),
        specification: { portfolio: { ...portfolio, automatic_research: { version } } },
      }}
    />
  )
  expect(screen.getByText(title)).toBeVisible()
  expect(screen.getByText(/4 of 18 symbols/)).toBeVisible()
  expect(screen.getByRole('progressbar', { name: 'Indicator history prepared' })).toHaveAttribute(
    'value',
    '4'
  )
  expect(screen.queryByText('9 of 18 symbols checked')).not.toBeInTheDocument()
  expect(screen.queryByText('Already in OpenAlgo')).not.toBeInTheDocument()
})

it('reports a frozen walk-forward window check without calling it a shortlist search', () => {
  render(
    <ResearchRunProgress
      job={{
        ...job({ ...base, stage: 'validation' }),
        specification: {
          portfolio: { ...portfolio, automatic_research: { version: 'automatic-hypotheses-v8' } },
        },
      }}
    />
  )
  expect(screen.getByRole('status')).toHaveTextContent('Checking the next window')
  expect(screen.queryByText('Checking shortlisted settings')).not.toBeInTheDocument()
})
