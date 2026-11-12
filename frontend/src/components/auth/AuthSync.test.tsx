import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ResearchLayout } from '@/components/layout/ResearchLayout'
import Tools from '@/pages/Tools'
import { useAuthStore } from '@/stores/authStore'
import { useBrokerStore } from '@/stores/brokerStore'
import { useThemeStore } from '@/stores/themeStore'
import { AuthSync, syncBrokerSession } from './AuthSync'

afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.getState().logout()
})
describe('Research credential authentication', () => {
  it.each([
    false,
    true,
  ])('allows saved review when authenticated without broker, logged_in=%s', async (loggedIn) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          status: 'success',
          authenticated: true,
          logged_in: loggedIn,
          broker: null,
          user: 'research-user',
        }),
      })
    )
    render(
      <MemoryRouter initialEntries={['/scanner-research']}>
        <AuthSync>
          <Routes>
            <Route element={<ResearchLayout />}>
              <Route path="/scanner-research" element={<p>Saved research evidence</p>} />
            </Route>
            <Route path="/login" element={<p>Login required</p>} />
          </Routes>
        </AuthSync>
      </MemoryRouter>
    )
    expect(await screen.findByText('Saved research evidence')).toBeVisible()
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(useAuthStore.getState().user?.broker).toBeNull()
  })
  it('rejects unauthenticated research navigation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: 'success', authenticated: false, logged_in: false }),
      })
    )
    render(
      <MemoryRouter initialEntries={['/scanner-research']}>
        <AuthSync>
          <Routes>
            <Route element={<ResearchLayout />}>
              <Route path="/scanner-research" element={<p>Saved research evidence</p>} />
            </Route>
            <Route path="/login" element={<p>Login required</p>} />
          </Routes>
        </AuthSync>
      </MemoryRouter>
    )
    expect(await screen.findByText('Login required')).toBeVisible()
  })
  it('opens the Tools catalog and Scanner Research entry without a broker', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue({
          ok: true,
          json: async () => ({
            status: 'success',
            authenticated: true,
            logged_in: false,
            broker: null,
            user: 'research-user',
          }),
        })
    )
    render(
      <MemoryRouter initialEntries={['/tools']}>
        <AuthSync>
          <Routes>
            <Route element={<ResearchLayout />}>
              <Route path="/tools" element={<Tools />} />
            </Route>
            <Route path="/login" element={<p>Login required</p>} />
          </Routes>
        </AuthSync>
      </MemoryRouter>
    )
    expect(await screen.findByRole('link', { name: /Backtest & Optimize/ })).toHaveAttribute(
      'href',
      '/scanner-research'
    )
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
  })
})

function respondWith(body: unknown, ok = true) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok, json: async () => body }) as Response)
  )
}

describe('syncBrokerSession', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, apiKey: null, isAuthenticated: false })
    vi.spyOn(useBrokerStore.getState(), 'fetchCapabilities').mockResolvedValue()
    vi.spyOn(useThemeStore.getState(), 'syncAppMode').mockResolvedValue()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads the API key, capabilities and app mode after an in-app login', async () => {
    respondWith({
      status: 'success',
      logged_in: true,
      user: 'trader',
      broker: 'angel',
      api_key: 'key-123',
      active_sessions: 1,
    })
    await expect(syncBrokerSession()).resolves.toBe(true)
    expect(useAuthStore.getState().apiKey).toBe('key-123')
    expect(useAuthStore.getState().user?.broker).toBe('angel')
    expect(useBrokerStore.getState().fetchCapabilities).toHaveBeenCalled()
    expect(useThemeStore.getState().syncAppMode).toHaveBeenCalled()
  })

  it('changes nothing when the server reports no broker session', async () => {
    respondWith({ status: 'success', authenticated: true, logged_in: false })
    await expect(syncBrokerSession()).resolves.toBe(false)
    expect(useAuthStore.getState().apiKey).toBeNull()
  })

  it('changes nothing when the request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline')
      })
    )
    await expect(syncBrokerSession()).resolves.toBe(false)
    expect(useAuthStore.getState().apiKey).toBeNull()
  })
})
