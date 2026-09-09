import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { researchDefaults } from '@/lib/researchDraft'
import { PreflightReview } from './PreflightReview'

describe('material preflight evidence', () => {
  it('shows complete earlier, gap and later dates/counts with fixed assumptions before submission', () => {
    const payload = {
      source_id: 'source',
      kind: 'research' as const,
      config: { ...researchDefaults, trailing_enabled: true, trailing_pct: 0, priority_seed: 89 },
      specification: {
        intent: 'select_earlier',
        prior_explored: true,
        min_train_closed: 10,
        variants: [{ name: 'Fee stress', changes: { cost_bps: 35 } }],
      },
    }
    const windows = [
      {
        fold: 1,
        train_from: '2025-01-01',
        train_end: '2025-12-31',
        train_sessions: 245,
        training_signals: 817,
        gap_from: '2026-01-01',
        gap_to: '2026-01-07',
        gap_sessions: 5,
        test_from: '2026-01-08',
        test_end: '2026-02-04',
        test_sessions: 20,
        possible_entries: 41,
      },
    ]
    render(
      <PreflightReview
        payload={payload}
        receipt={{
          config: payload.config,
          specification: payload.specification,
          windows,
          planned_evaluations: 27,
        }}
      />
    )
    const window = within(screen.getByRole('region', { name: 'Preflight window 1' }))
    expect(window.getByText('2025-01-01 to 2025-12-31')).toBeVisible()
    expect(window.getByText('245 exchange sessions · 817 signals')).toBeVisible()
    expect(window.getByText('2026-01-01 to 2026-01-07')).toBeVisible()
    expect(window.getByText('2026-01-08 to 2026-02-04')).toBeVisible()
    expect(window.getByText(/20 exchange sessions · at most 41 possible/)).toBeVisible()
    expect(screen.getByText('Fee stress')).toBeVisible()
    expect(screen.getByText('35 basis points')).toBeVisible()
    expect(screen.getByText('89')).toBeVisible()
    expect(screen.queryByText(/additional entries retained in export/)).not.toBeInTheDocument()
  })
})
