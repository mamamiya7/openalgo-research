import { expect, it, vi } from 'vitest'
import { webClient } from './client'
import { researchLiveProgress } from './researchLiveProgress'

vi.mock('./client', () => ({ webClient: { get: vi.fn() } }))

it('reads live progress through the authenticated client with an encoded identity and abort signal', async () => {
  const data = { version: 'research-live-progress-v1', job_id: 'job / one' }
  vi.mocked(webClient.get).mockResolvedValue({ data })
  const controller = new AbortController()
  expect(await researchLiveProgress.get('job / one', controller.signal)).toBe(data)
  expect(webClient.get).toHaveBeenCalledWith(
    '/scanner-research/api/portfolio/jobs/job%20%2F%20one/live-progress',
    { signal: controller.signal, timeout: 15000 }
  )
})
