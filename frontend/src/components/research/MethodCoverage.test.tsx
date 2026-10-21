import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  type ResearchMethodCoverageRow,
  type ResearchMethodCoverageView,
  researchMethodCoverage,
} from '@/api/researchMethodCoverage'
import { useAuthStore } from '@/stores/authStore'
import { MethodCoverage } from './MethodCoverage'

vi.mock('@/api/researchMethodCoverage', () => ({ researchMethodCoverage: { get: vi.fn() } }))
const version = 'automatic-hypotheses-v8'
function family(overrides: Partial<ResearchMethodCoverageRow> = {}): ResearchMethodCoverageRow {
  return {
    id: 'ema:all',
    registered: 2,
    proposed: 3,
    evaluated: 2,
    reused: 1,
    retained_histories: 2,
    unavailable_histories: 0,
    untested: 1,
    budget_deferred: 0,
    status: 'evaluated',
    data_status: 'no_unknown_entries_reported',
    unknown_entry_checks: 0,
    ...overrides,
  }
}
function receipt(): ResearchMethodCoverageView {
  return {
    version: 'research-method-coverage-view-v1',
    job_id: 'study-job',
    available: true,
    reason: null,
    checkpoint_artifact: 'checkpoint',
    result_artifact: 'result',
    coverage: {
      version: 'research-method-coverage-v1',
      status: 'available',
      source: {
        inputs_artifact: 'input',
        recipe_id: 'recipe',
        recipe_version: version,
        stock_evidence_id: 'stock',
        mandate_id: 'mandate',
        checkpoint_id: 'checkpoint',
        manifest_id: 'manifest',
        search_identity: 'search',
      },
      registry: {
        version: 'research-method-registry-v1',
        id: 'registry',
        recipe_version: version,
        catalog_id: 'catalog',
        family_count: 32,
        hypothesis_count: 92,
        methods: [
          {
            id: 'ema',
            label: 'Exponential moving average',
            formula: 'EMA uses earlier completed session closes.',
            requirements: ['100 recorded stock sessions'],
            observations: 'Earlier completed sessions',
            implementation: 'native',
            availability: 'Before entry',
            missing: 'Exclude unknown',
            equality: 'Strict above',
            comparison_policy: { kind: 'strict_above' },
            support: {
              engine: 'vectorbt',
              asset_profile: 'NSE_long_cash_equities',
              execution_intervals: ['D', '1m'],
              feature_interval: 'D',
            },
            id_hash: 'ema-hash',
          },
        ],
        families: [
          {
            id: 'ema:all',
            label: 'Stock trend',
            method_ids: ['ema'],
            registered_hypothesis_ids: ['ema10', 'ema20'],
            formula: 'ema',
            domain: [],
          },
          {
            id: 'rsi:all',
            label: 'RSI',
            method_ids: [],
            registered_hypothesis_ids: ['rsi7'],
            formula: 'rsi',
            domain: [],
          },
        ],
        blocked_families: [
          {
            id: 'options',
            label: 'Options pricing',
            status: 'unsupported',
            reason: 'Historical option chains are not retained.',
          },
        ],
      },
      fits: [
        {
          id: 'fit1',
          status: 'complete',
          proposals: 48,
          proposal_limit: 48,
          families: [family({ evaluated: 1, reused: 0, untested: 1 })],
        },
        {
          id: 'fit2',
          status: 'pending',
          proposals: 0,
          proposal_limit: 48,
          families: [
            family({
              evaluated: 0,
              proposed: 0,
              reused: 0,
              untested: 2,
              status: 'pending',
              data_status: 'not_observed',
            }),
          ],
        },
      ],
      families: [
        family(),
        family({
          id: 'rsi:all',
          registered: 1,
          proposed: 0,
          evaluated: 0,
          reused: 0,
          untested: 1,
          status: 'budget_deferred',
          data_status: 'not_observed',
        }),
      ],
      totals: {
        proposals: 3,
        evaluated: 2,
        reused: 1,
        retained_histories: 2,
        unavailable_histories: 0,
        registered_families: 32,
        registered_hypotheses: 92,
      },
      id: 'coverage',
      limitations: [],
    },
  }
}
const clients: QueryClient[] = []
function mount(props: { jobId?: string; recipeVersion?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  let current = { jobId: 'study-job', recipeVersion: version, ...props }
  const tree = () => (
    <QueryClientProvider client={client}>
      <MethodCoverage {...current} />
    </QueryClientProvider>
  )
  const view = render(tree())
  return {
    ...view,
    client,
    change: (next: typeof props) => {
      current = { ...current, ...next }
      view.rerender(tree())
    },
  }
}
async function open() {
  const trigger = screen.getByRole('button', { name: 'Methods considered', exact: true })
  await userEvent.click(trigger)
  return trigger
}
beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({ user: { username: 'method-owner' } })
  vi.mocked(researchMethodCoverage.get).mockResolvedValue(receipt())
})
afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
  vi.useRealTimers()
})

describe('recorded method coverage', () => {
  it('fetches only after an explicit open, scopes to the account/job and drops the closed cache', async () => {
    const view = mount()
    expect(researchMethodCoverage.get).not.toHaveBeenCalled()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    const trigger = await open()
    expect(await screen.findByRole('table', { name: 'Methods considered' })).toBeVisible()
    expect(researchMethodCoverage.get).toHaveBeenCalledWith('study-job', expect.any(AbortSignal))
    expect(screen.getByText('1 method family tested')).toBeVisible()
    expect(
      screen.queryByText(/32.*passed|92.*tested|confidence|optimality/i)
    ).not.toBeInTheDocument()
    expect(screen.getByText(/Testing does not mean an improvement was found/)).toBeVisible()
    await userEvent.click(trigger)
    expect(trigger).toHaveFocus()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(researchMethodCoverage.get).toHaveBeenCalledTimes(1)
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(view.client.getQueryCache().getAll()).toHaveLength(0)
    await open()
    await screen.findByRole('table')
    expect(researchMethodCoverage.get).toHaveBeenCalledTimes(2)
  })

  it.each([
    undefined,
    'automatic-hypotheses-v7',
    'automatic-hypotheses-v99',
  ])('does not expose or request unsupported saved recipe %s', (recipeVersion) => {
    render(<MethodCoverage jobId="study-job" recipeVersion={recipeVersion} />)
    expect(screen.queryByRole('button', { name: 'Methods considered' })).not.toBeInTheDocument()
    expect(researchMethodCoverage.get).not.toHaveBeenCalled()
  })

  it('has no disclosure without a durable job identity', () => {
    render(<MethodCoverage recipeVersion={version} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(researchMethodCoverage.get).not.toHaveBeenCalled()
  })

  it('keeps formulas and prerequisites in one inline drilldown and reports actual missing entry data only', async () => {
    const data = receipt()
    data.coverage!.families[0].data_status = 'unknown_entries_reported'
    data.coverage!.families[0].unknown_entry_checks = 1234
    vi.mocked(researchMethodCoverage.get).mockResolvedValue(data)
    mount()
    await open()
    const table = await screen.findByRole('table')
    expect(within(table).getByText('Some entry checks lacked feature data')).toBeVisible()
    expect(screen.queryByText(/1,234|all data available|no missing data/i)).not.toBeInTheDocument()
    expect(screen.queryByText('EMA uses earlier completed session closes.')).not.toBeInTheDocument()
    const button = within(table).getByRole('button', { name: 'Stock trend' })
    await userEvent.click(button)
    expect(button).toHaveFocus()
    expect(screen.getByText('EMA uses earlier completed session closes.')).toBeVisible()
    expect(screen.getByText('Needs: 100 recorded stock sessions')).toBeVisible()
    expect(screen.getByText('Historical option chains are not retained.')).not.toBeVisible()
    await userEvent.click(button)
    expect(button).toHaveFocus()
    expect(screen.queryByText('EMA uses earlier completed session closes.')).not.toBeInTheDocument()
  })

  it('filters recorded fit counters locally without calculating, refetching or declaring untested methods passed', async () => {
    mount()
    await open()
    const table = await screen.findByRole('table')
    expect(within(table).getByRole('row', { name: 'Stock trend 2 1 1' })).toBeVisible()
    const select = screen.getByRole('combobox', { name: 'Method checks for' })
    await userEvent.selectOptions(select, 'fit1')
    expect(within(table).getByRole('row', { name: 'Stock trend 1 0 1' })).toBeVisible()
    await userEvent.selectOptions(select, 'fit2')
    expect(within(table).getByRole('row', { name: 'Stock trend 0 0 2' })).toBeVisible()
    expect(screen.getByText('0 method families tested')).toBeVisible()
    expect(screen.queryByText(/lacked feature data|all data available/i)).not.toBeInTheDocument()
    expect(researchMethodCoverage.get).toHaveBeenCalledTimes(1)
  })

  it('shows unknown counters as unavailable rather than fabricating zeros', async () => {
    const data = receipt()
    data.coverage!.families[0].evaluated = undefined as unknown as number
    data.coverage!.families[0].reused = Number.NaN
    data.coverage!.families[0].untested = -1
    vi.mocked(researchMethodCoverage.get).mockResolvedValue(data)
    mount()
    await open()
    const table = await screen.findByRole('table')
    expect(within(table).getByRole('row', { name: 'Stock trend — — —' })).toBeVisible()
    expect(screen.getByText('Recorded method checks')).toBeVisible()
    expect(screen.queryByText(/0 method families tested/)).not.toBeInTheDocument()
  })

  it('names repeat fits and distinguishes skipped work from pending work', async () => {
    const data = receipt()
    data.coverage!.source.recipe_version = 'automatic-hypotheses-v15'
    data.coverage!.registry.recipe_version = 'automatic-hypotheses-v15'
    data.coverage!.fits.push({
      id: 'search_seed1',
      status: 'not_run',
      proposals: 0,
      proposal_limit: 68,
      families: [],
    })
    vi.mocked(researchMethodCoverage.get).mockResolvedValue(data)
    mount({ recipeVersion: 'automatic-hypotheses-v15' })
    await open()
    const select = await screen.findByRole('combobox', { name: 'Method checks for' })
    expect(
      within(select).getByRole('option', { name: 'Search repeat 1 (not needed)' })
    ).toBeInTheDocument()
    await userEvent.selectOptions(select, 'search_seed1')
    expect(screen.getByText('Extra search not needed')).toBeVisible()
    expect(researchMethodCoverage.get).toHaveBeenCalledTimes(1)
  })

  it('preserves a server explanation for unavailable metadata without fake counts or retry loops', async () => {
    vi.mocked(researchMethodCoverage.get).mockResolvedValue({
      ...receipt(),
      available: false,
      coverage: undefined,
      reason: 'Saved method details could not be verified; saved results are unchanged.',
    })
    mount()
    await open()
    expect(
      await screen.findByText(
        'Saved method details could not be verified; saved results are unchanged.'
      )
    ).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(researchMethodCoverage.get).toHaveBeenCalledTimes(1)
  })

  it('does not invent tested counts when no method accounts have been recorded', async () => {
    const data = receipt()
    data.coverage!.families = []
    data.coverage!.fits = []
    vi.mocked(researchMethodCoverage.get).mockResolvedValue(data)
    mount()
    await open()
    expect(await screen.findByText('No method checks recorded yet.')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByText('0 method families tested')).not.toBeInTheDocument()
  })

  it.each([
    undefined,
    'method_coverage_changed',
  ])('retries only on request after a metadata error %s', async (code) => {
    vi.mocked(researchMethodCoverage.get)
      .mockRejectedValueOnce(
        code
          ? { isAxiosError: true, response: { status: 409, data: { code } } }
          : new Error('network failed')
      )
      .mockResolvedValue(receipt())
    mount()
    await open()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      code ? 'Saved research changed' : 'could not be loaded'
    )
    expect(researchMethodCoverage.get).toHaveBeenCalledTimes(1)
    await userEvent.click(
      screen.getByRole('button', { name: code ? 'Reload methods' : 'Try again' })
    )
    expect(await screen.findByRole('table')).toBeVisible()
    expect(researchMethodCoverage.get).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: 'Methods considered', exact: true })).toHaveFocus()
  })

  it('aborts open requests and discards old-account/job views on identity changes and unmount', async () => {
    vi.mocked(researchMethodCoverage.get).mockImplementation(() => new Promise(() => {}))
    const view = mount()
    await open()
    const first = vi.mocked(researchMethodCoverage.get).mock.calls[0][1]!
    view.change({ jobId: 'other-job' })
    expect(first.aborted).toBe(true)
    expect(screen.getByRole('button', { name: 'Methods considered' })).toHaveAttribute(
      'aria-expanded',
      'false'
    )
    await open()
    expect(researchMethodCoverage.get).toHaveBeenLastCalledWith(
      'other-job',
      expect.any(AbortSignal)
    )
    const second = vi.mocked(researchMethodCoverage.get).mock.calls[1][1]!
    act(() => useAuthStore.setState({ user: { username: 'other-owner' } }))
    expect(second.aborted).toBe(true)
    await open()
    const third = vi.mocked(researchMethodCoverage.get).mock.calls[2][1]!
    view.unmount()
    expect(third.aborted).toBe(true)
  })

  it('aborts a pending request when the disclosure closes', async () => {
    vi.mocked(researchMethodCoverage.get).mockImplementation(() => new Promise(() => {}))
    mount()
    const trigger = await open()
    const signal = vi.mocked(researchMethodCoverage.get).mock.calls[0][1]!
    await userEvent.click(trigger)
    expect(signal.aborted).toBe(true)
    expect(trigger).toHaveFocus()
  })

  it('does not poll or refetch because window focus or network reconnect changes', async () => {
    vi.useFakeTimers()
    const client = new QueryClient()
    clients.push(client)
    const view = render(
      <QueryClientProvider client={client}>
        <MethodCoverage jobId="study-job" recipeVersion={version} />
      </QueryClientProvider>
    )
    const button = screen.getByRole('button', { name: 'Methods considered' })
    await act(async () => {
      button.click()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(researchMethodCoverage.get).toHaveBeenCalledTimes(1)
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      window.dispatchEvent(new Event('online'))
      await vi.advanceTimersByTimeAsync(30000)
    })
    expect(researchMethodCoverage.get).toHaveBeenCalledTimes(1)
    view.unmount()
  })

  it('has accessible expanded table and inline method details', async () => {
    mount()
    await open()
    await userEvent.click(await screen.findByRole('button', { name: 'Stock trend' }))
    expect((await axe(document.body)).violations).toEqual([])
  })
})

it.each([
  401, 404,
])('preserves an HTTP %s failure instead of disguising it as unrecorded methods', async (status) => {
  vi.mocked(researchMethodCoverage.get).mockRejectedValue({
    isAxiosError: true,
    response: { status, data: { error: 'unavailable' } },
  })
  mount()
  await open()
  expect(await screen.findByRole('alert')).toHaveTextContent('Method checks could not be loaded.')
  expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
  expect(
    screen.queryByText('Method details were not recorded for this run.')
  ).not.toBeInTheDocument()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})

it('does not display a method receipt returned for a different saved job', async () => {
  vi.mocked(researchMethodCoverage.get).mockResolvedValue({
    ...receipt(),
    job_id: 'another-private-job',
  })
  mount()
  await open()
  expect(await screen.findByText('Method details were not recorded for this run.')).toBeVisible()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(screen.queryByText('Stock trend')).not.toBeInTheDocument()
})

it('explains combined stock and market requirements without exposing internal input names', async () => {
  const data = receipt()
  const first = data.coverage!.registry.methods[0]
  first.requirements = ['stock_daily_close']
  data.coverage!.registry.methods.push({
    ...first,
    id: 'market_trend',
    label: 'Nifty trend',
    formula: 'Completed Nifty closes must satisfy the registered trend condition.',
    requirements: ['NIFTY_NSE_INDEX_daily_close'],
  })
  data.coverage!.registry.families[0].method_ids.push('market_trend')
  vi.mocked(researchMethodCoverage.get).mockResolvedValue(data)
  mount()
  await open()
  await userEvent.click(await screen.findByRole('button', { name: 'Stock trend' }))
  expect(screen.getByText('All conditions must be met.')).toBeVisible()
  expect(screen.getByText('Needs: Daily stock closes')).toBeVisible()
  expect(screen.getByText('Needs: Daily Nifty closes')).toBeVisible()
  expect(
    screen.queryByText(/stock_daily_close|NIFTY_NSE_INDEX_daily_close/)
  ).not.toBeInTheDocument()
})

it('opens the expanded method programme and refuses mismatched saved definitions', async () => {
  const data = receipt()
  data.coverage!.source.recipe_version = 'automatic-hypotheses-v9'
  data.coverage!.registry.recipe_version = 'automatic-hypotheses-v9'
  data.coverage!.registry.hypothesis_count = 108
  vi.mocked(researchMethodCoverage.get).mockResolvedValue(data)
  mount({ recipeVersion: 'automatic-hypotheses-v9' })
  await open()
  expect(await screen.findByRole('table', { name: 'Methods considered' })).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Methods considered' }))
  data.coverage!.registry.recipe_version = 'automatic-hypotheses-v8'
  await open()
  expect(await screen.findByText('Method details were not recorded for this run.')).toBeVisible()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})
