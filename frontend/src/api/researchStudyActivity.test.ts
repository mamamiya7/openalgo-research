import { describe, expect, it, vi } from 'vitest'
import { webClient } from './client'
import { researchStudyActivity } from './researchStudyActivity'

vi.mock('./client', () => ({ webClient: { get: vi.fn() } }))

describe('study activity API', () => {
  it('uses the owner-authenticated client with bounded paging and an abortable timeout', async () => {
    const data = { version: 'research-study-activity-v1', rows: [] }
    vi.mocked(webClient.get).mockResolvedValue({ data })
    const controller = new AbortController()
    expect(
      await researchStudyActivity.get(
        'job / one',
        { before: 50, execution: 'attempt-id' },
        controller.signal
      )
    ).toBe(data)
    expect(webClient.get).toHaveBeenCalledWith(
      '/scanner-research/api/portfolio/jobs/job%20%2F%20one/activity',
      {
        params: { limit: 25, before: 50, execution: 'attempt-id' },
        signal: controller.signal,
        timeout: 15000,
      }
    )
  })
})
