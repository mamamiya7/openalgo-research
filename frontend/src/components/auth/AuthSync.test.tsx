import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ResearchLayout } from '@/components/layout/ResearchLayout'
import Tools from '@/pages/Tools'
import { useAuthStore } from '@/stores/authStore'
import { AuthSync } from './AuthSync'

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
