import { describe, expect, it, vi } from 'vitest'
import { webClient } from './client'
import { researchPartialStudy } from './researchPartialStudy'

vi.mock('./client', () => ({ webClient: { get: vi.fn() } }))

describe('partial study API', () => {
  it('uses only an authenticated bounded abortable read and pins later pages to saved evidence', async () => {
    const data = { version: 'research-partial-study-v1', rows: [] }
    vi.mocked(webClient.get).mockResolvedValue({ data })
    const controller = new AbortController()
    expect(
      await researchPartialStudy.get(
        'job / one',
        { offset: 25, expected_checkpoint: 'saved-checkpoint' },
        controller.signal
      )
    ).toBe(data)
    expect(webClient.get).toHaveBeenCalledWith(
      '/scanner-research/api/portfolio/jobs/job%20%2F%20one/partial-study',
      {
        params: { limit: 25, offset: 25, expected_checkpoint: 'saved-checkpoint' },
        signal: controller.signal,
        timeout: 15000,
      }
    )
  })
})
