import { beforeEach, describe, expect, it, vi } from 'vitest'
import { webClient } from './client'
import { researchComparisons } from './researchComparisons'

vi.mock('./client', () => ({ webClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }))
beforeEach(() => {
  vi.clearAllMocks()
  for (const method of ['get', 'post', 'patch'] as const)
    vi.mocked(webClient[method]).mockResolvedValue({ data: { ok: true } })
})
describe('native saved comparison client', () => {
  it('bounds reads and addresses exact experiment, comparison and member with cancellation', async () => {
    const signal = new AbortController().signal
    await researchComparisons.list('a / b', 20, signal)
    expect(webClient.get).toHaveBeenCalledWith(
      '/scanner-research/api/library/experiments/a%20%2F%20b/comparisons',
      { params: { limit: 20, offset: 20 }, signal, timeout: 15000 }
    )
    await researchComparisons.member('e', 'c / d', 'member / one', signal)
    expect(webClient.get).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/e/comparisons/c%20%2F%20d/members/member%20%2F%20one',
      { signal, timeout: 15000 }
    )
  })
  it('sends ordered identities, explicit reference and stable retry token without client evidence', async () => {
    const signal = new AbortController().signal
    const request = {
      request_id: 'same-request',
      candidate_ids: ['b', 'a'],
      reference_candidate_id: 'a',
    }
    await researchComparisons.save('e', request, signal)
    expect(webClient.post).toHaveBeenCalledWith(
      '/scanner-research/api/library/experiments/e/comparisons',
      request,
      { signal, timeout: 15000 }
    )
    await researchComparisons.update(
      'e',
      'c',
      { revision: 3, name: 'Review', note: 'Keep original membership' },
      signal
    )
    expect(webClient.patch).toHaveBeenCalledWith(
      '/scanner-research/api/library/experiments/e/comparisons/c',
      { revision: 3, name: 'Review', note: 'Keep original membership' },
      { signal, timeout: 15000 }
    )
  })
})
