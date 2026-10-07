import { beforeEach, describe, expect, it, vi } from 'vitest'
import { webClient } from './client'
import { decisionOpeningTarget, researchDecisions } from './researchDecisions'

vi.mock('./client', () => ({ webClient: { get: vi.fn(), post: vi.fn() } }))
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(webClient.get).mockResolvedValue({ data: {} })
  vi.mocked(webClient.post).mockResolvedValue({ data: {} })
})
describe('native decisions client', () => {
  it('fences a direct save against the displayed report without changing opening targets', async () => {
    const target = {
      job_id: 'job',
      config_id: 'config',
      expected_report: { result_artifact: 'frozen', analysis_artifact: null },
    }
    await researchDecisions.context('e', target)
    expect(webClient.get).toHaveBeenLastCalledWith(
      expect.any(String),
      expect.objectContaining({
        params: { config_id: 'config', expected_result_artifact: 'frozen', limit: 20, offset: 0 },
      })
    )
    const data = { revision: 0, request_id: 'save', state: 'keep' as const, reason: '' }
    await researchDecisions.save('e', target, data)
    expect(webClient.post).toHaveBeenLastCalledWith(
      expect.any(String),
      { ...data, expected_report: target.expected_report },
      expect.any(Object)
    )
    expect(decisionOpeningTarget(target, 'later')).toEqual({
      kind: 'direct_report',
      job_id: 'job',
      config_id: 'config',
      evaluation_id: 'later',
    })
  })
  it('uses report routes and carries the exact configuration for a direct decision', async () => {
    const signal = new AbortController().signal
    const target = { job_id: 'saved / report', config_id: 'alternative' }
    await researchDecisions.context('idea', target, 0, signal)
    expect(webClient.get).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/idea/results/saved%20%2F%20report/decision',
      { params: { config_id: 'alternative', limit: 20, offset: 0 }, signal, timeout: 15000 }
    )
    const data = {
      revision: 0,
      request_id: 'save',
      state: 'revisit' as const,
      reason: '',
      evaluation_id: null,
    }
    await researchDecisions.save('idea', target, data, signal)
    expect(webClient.post).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/idea/results/saved%20%2F%20report/decisions',
      data,
      { params: { config_id: 'alternative' }, signal, timeout: 15000 }
    )
    await researchDecisions.preview('idea', target, 'later / pin', signal)
    expect(webClient.get).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/idea/results/saved%20%2F%20report/decision/evaluations/later%20%2F%20pin',
      { params: { config_id: 'alternative' }, signal, timeout: 15000 }
    )
  })
  it('bounds owner-scoped reads and URL encodes exact later evidence selectors', async () => {
    const signal = new AbortController().signal
    await researchDecisions.context(
      'e / 1',
      { comparison_id: 'c / 2', member_id: 'm/3' },
      20,
      signal
    )
    expect(webClient.get).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/e%20%2F%201/comparisons/c%20%2F%202/members/m%2F3/decision',
      { params: { limit: 20, offset: 20 }, signal, timeout: 15000 }
    )
    await researchDecisions.preview(
      'e',
      { comparison_id: 'c', member_id: 'm' },
      'opaque /+?=',
      signal
    )
    expect(webClient.get).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/e/comparisons/c/members/m/decision/evaluations/opaque%20%2F%2B%3F%3D',
      { signal, timeout: 15000 }
    )
    await researchDecisions.list('e', 'keep', 40, signal)
    expect(webClient.get).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/e/decisions',
      { params: { state: 'keep', limit: 20, offset: 40 }, signal, timeout: 15000 }
    )
    await researchDecisions.history('e', 'd', 20, signal)
    expect(webClient.get).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/e/decisions/d/history',
      { params: { limit: 20, offset: 20 }, signal, timeout: 15000 }
    )
    await researchDecisions.report('e', 'd', 'event/1', 'evaluation', signal)
    expect(webClient.get).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/e/decisions/d/events/event%2F1/report',
      { params: { evidence: 'evaluation' }, signal, timeout: 15000 }
    )
  })
  it('posts identity, revision and request token without calculated values or invented provenance', async () => {
    const signal = new AbortController().signal
    const request = {
      request_id: 'retry',
      revision: 2,
      state: 'keep' as const,
      reason: 'Retest costs',
      evaluation_id: 'exact-later',
    }
    await researchDecisions.save('e', { comparison_id: 'c', member_id: 'm' }, request, signal)
    expect(webClient.post).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/e/comparisons/c/members/m/decisions',
      request,
      { signal, timeout: 15000 }
    )
    const intent = {
      request_id: 'open-retry',
      target: {
        kind: 'comparison_member' as const,
        comparison_id: 'c',
        member_id: 'm',
        evaluation_id: 'exact-later',
      },
    }
    await researchDecisions.opened('e', intent, signal)
    expect(webClient.post).toHaveBeenLastCalledWith(
      '/scanner-research/api/library/experiments/e/evidence/opened',
      intent,
      { signal, timeout: 15000 }
    )
  })
})
