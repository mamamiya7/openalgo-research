import { expect, it, vi } from 'vitest'
import { webClient } from './client'
import { researchTradeChart } from './researchTradeChart'

vi.mock('./client', () => ({ webClient: { get: vi.fn() } }))

it('reads only the pinned report, original trade index and requested candle page', async () => {
  vi.mocked(webClient.get).mockResolvedValue({ data: { symbol: 'AAA' } })
  const signal = new AbortController().signal
  await researchTradeChart.get(
    { jobId: 'saved/job', resultArtifact: 'a'.repeat(64), tradeIndex: 37, period: 'evaluation' },
    1500,
    signal
  )
  expect(webClient.get).toHaveBeenCalledExactlyOnceWith(
    '/scanner-research/api/portfolio/jobs/saved%2Fjob/trades/37/chart',
    {
      params: {
        expected_result_artifact: 'a'.repeat(64),
        period: 'evaluation',
        offset: 1500,
        limit: 1500,
      },
      signal,
      timeout: 30000,
    }
  )
})
