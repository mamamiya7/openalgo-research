import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { ResearchLayout } from './ResearchLayout'

vi.mock('./Layout', () => ({ Layout: () => <nav aria-label="OpenAlgo app navigation" /> }))

beforeEach(() => {
  useAuthStore
    .getState()
    .setUser({ username: 'research-user', broker: null, isLoggedIn: false, loginTime: null })
  useThemeStore.setState({ mode: 'light', appMode: 'live' })
  document.documentElement.classList.remove('dark')
})
afterEach(() => {
  vi.restoreAllMocks()
  useAuthStore.getState().logout()
  useThemeStore.setState({ mode: 'light', appMode: 'live' })
  document.documentElement.classList.remove('dark')
})

describe('Research appearance control', () => {
  it('keeps broker connection accessible while retained research is available', () => {
    render(
      <MemoryRouter>
        <ResearchLayout />
      </MemoryRouter>
    )
    expect(screen.getByRole('link', { name: 'Connect broker' })).toHaveAttribute('href', '/broker')
  })
  it('uses the full OpenAlgo navigation when the broker session is connected', () => {
    useAuthStore.getState().setUser({
      username: 'research-user',
      broker: 'fyers',
      isLoggedIn: true,
      loginTime: null,
    })
    render(
      <MemoryRouter>
        <ResearchLayout />
      </MemoryRouter>
    )
    expect(screen.getByRole('navigation', { name: 'OpenAlgo app navigation' })).toBeInTheDocument()
    expect(
      screen.queryByRole('navigation', { name: 'Research navigation' })
    ).not.toBeInTheDocument()
  })
  it('changes appearance with the keyboard without changing trading mode or requesting the server', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    render(
      <MemoryRouter>
        <ResearchLayout />
      </MemoryRouter>
    )
    screen.getByRole('button', { name: 'Switch to dark mode' }).focus()
    await user.keyboard('{Enter}')
    expect(document.documentElement).toHaveClass('dark')
    expect(screen.getByRole('button', { name: 'Switch to light mode' })).toHaveFocus()
    expect(useThemeStore.getState().appMode).toBe('live')
    expect(fetchSpy).not.toHaveBeenCalled()
  })
  it('honors the native appearance restriction in analyzer mode', () => {
    useThemeStore.setState({ appMode: 'analyzer' })
    render(
      <MemoryRouter>
        <ResearchLayout />
      </MemoryRouter>
    )
    expect(screen.getByRole('button', { name: 'Switch to dark mode' })).toBeDisabled()
    expect(useThemeStore.getState().appMode).toBe('analyzer')
  })
})
