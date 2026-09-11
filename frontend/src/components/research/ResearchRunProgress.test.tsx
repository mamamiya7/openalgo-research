import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PortfolioRequest } from '@/api/portfolioResearch'
import type { ResearchActivity } from '@/api/scannerResearch'
import { researchDefaults } from '@/lib/researchDraft'
import { ResearchRunProgress, ResearchUploadProgress } from './ResearchRunProgress'

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

describe('Research progress journey', () => {
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
