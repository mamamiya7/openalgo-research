import { beforeEach, describe, expect, it, vi } from 'vitest'
import { webClient } from './client'
import { researchShortlist } from './researchShortlist'

vi.mock('./client', () => ({
  webClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
beforeEach(() => {
  vi.clearAllMocks()
  for (const method of ['get', 'post', 'patch', 'delete'] as const)
    vi.mocked(webClient[method]).mockResolvedValue({ data: { ok: true } })
})
describe('native shortlist client', () => {
  it('uses bounded owner-authenticated list reads with aborts', async () => {
    const controller = new AbortController()
    await researchShortlist.list(
      'experiment / one',
      { offset: 20, job_id: 'job', config_id: 'config' },
      controller.signal
    )
    expect(webClient.get).toHaveBeenCalledWith(
      '/scanner-research/api/library/experiments/experiment%20%2F%20one/shortlist',
      {
        params: { limit: 20, offset: 20, job_id: 'job', config_id: 'config' },
        signal: controller.signal,
        timeout: 15000,
      }
    )
    await researchShortlist.get('e', 'candidate / one', controller.signal)
    expect(webClient.get).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/e/shortlist/candidate%20%2F%20one',
      { signal: controller.signal, timeout: 15000 }
    )
  })
  it('sends only the actual source/proposal identity when saving', async () => {
    const signal = new AbortController().signal
    await researchShortlist.save(
      'e',
      { job_id: 'study', config_id: 'config', proposal_number: 0 },
      signal
    )
    expect(webClient.post).toHaveBeenCalledWith(
      '/scanner-research/api/library/experiments/e/shortlist',
      { job_id: 'study', config_id: 'config', proposal_number: 0 },
      { signal, timeout: 15000 }
    )
  })
  it('retains item revisions and uses a body for removal without deleting evidence', async () => {
    const signal = new AbortController().signal
    await researchShortlist.update(
      'e',
      'c',
      { revision: 4, name: 'Candidate', note: 'Review' },
      signal
    )
    expect(webClient.patch).toHaveBeenCalledWith(
      '/scanner-research/api/library/experiments/e/shortlist/c',
      { revision: 4, name: 'Candidate', note: 'Review' },
      { signal, timeout: 15000 }
    )
    await researchShortlist.remove('e', 'c', 5, signal)
    expect(webClient.delete).toHaveBeenCalledWith(
      '/scanner-research/api/library/experiments/e/shortlist/c',
      { data: { revision: 5 }, signal, timeout: 15000 }
    )
  })
})
