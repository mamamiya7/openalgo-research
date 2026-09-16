import { beforeEach, describe, expect, it, vi } from 'vitest'
import { webClient } from './client'
import { portfolioResearch } from './portfolioResearch'

vi.mock('./client', () => ({ webClient: { post: vi.fn() } }))
beforeEach(() => vi.mocked(webClient.post).mockResolvedValue({ data: { status: 'queued' } }))
describe('portfolio analysis request contract', () => {
  it('submits a condition replay with its exact evidence, idempotency token and abort signal', async () => {
    const request = {
      experiment_id: 'experiment-a',
      strategy_id: 'a',
      dimension: 'trend',
      regime: 'up',
      analysis_artifact: 'analysis-a',
      period: 'validation',
      request_id: 'request-a',
    } as const
    const signal = new AbortController().signal
    await portfolioResearch.conditionReplay('saved/job', request, signal)
    expect(webClient.post).toHaveBeenLastCalledWith(
      '/scanner-research/api/portfolio/jobs/saved%2Fjob/condition-replay',
      request,
      { signal, timeout: 30000 }
    )
  })
  it('adds market conditions only when explicitly requested without changing existing argument order', async () => {
    await portfolioResearch.prepareAnalysis(
      'saved/job',
      ['a.stop_pct'],
      'AAA',
      'validation',
      undefined,
      true
    )
    expect(webClient.post).toHaveBeenLastCalledWith(
      '/scanner-research/api/portfolio/jobs/saved%2Fjob/analysis',
      { parameters: ['a.stop_pct'], symbol: 'AAA', period: 'validation', market_conditions: true },
      { timeout: 30000 }
    )
    await portfolioResearch.prepareAnalysis(
      'saved/job',
      undefined,
      undefined,
      'selection',
      undefined,
      false
    )
    expect(webClient.post).toHaveBeenLastCalledWith(
      '/scanner-research/api/portfolio/jobs/saved%2Fjob/analysis',
      { period: 'selection' },
      { timeout: 30000 }
    )
  })

  it('adds a benchmark descriptor only on the explicit preparation request', async () => {
    const benchmark = {
      symbol: 'NIFTY',
      exchange: 'NSE_INDEX',
      interval: 'D',
      role: 'benchmark',
    } as const
    await portfolioResearch.prepareAnalysis(
      'saved/job',
      ['a.stop_pct'],
      'AAA',
      'validation',
      benchmark
    )
    expect(webClient.post).toHaveBeenLastCalledWith(
      '/scanner-research/api/portfolio/jobs/saved%2Fjob/analysis',
      { parameters: ['a.stop_pct'], symbol: 'AAA', period: 'validation', benchmark },
      { timeout: 30000 }
    )
    await portfolioResearch.prepareAnalysis('saved/job', undefined, undefined, 'selection')
    expect(webClient.post).toHaveBeenLastCalledWith(
      '/scanner-research/api/portfolio/jobs/saved%2Fjob/analysis',
      { period: 'selection' },
      { timeout: 30000 }
    )
  })
})
