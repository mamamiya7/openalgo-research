import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import BrokerSelect from './BrokerSelect'

vi.mock('@/stores/authStore', () => ({ useAuthStore: () => ({ user: { username: 'trader' } }) }))
vi.mock('@/components/auth/BrokerAuthSignOut', () => ({
  BrokerAuthSignOut: () => <button type="button">Sign out</button>,
}))
vi.mock('@/components/auth/BrokerCredentialsSetup', () => ({
  BrokerCredentialsSetup: ({ open, onSaved }: { open: boolean; onSaved: () => void }) =>
    open ? (
      <button type="button" onClick={onSaved}>
        Save fixture credentials
      </button>
    ) : null,
}))

describe('BrokerSelect first setup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function mockConfig(key: string) {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        json: async () => ({
          status: 'success',
          broker_name: 'fyers',
          broker_api_key: key,
          redirect_url: 'http://127.0.0.1:5247/fyers/callback',
        }),
      })
    )
  }

  it('offers credential setup before any broker connection and requires restart after save', async () => {
    mockConfig('')
    render(<BrokerSelect />)
    const setup = await screen.findByRole('button', { name: 'Add broker credentials' })
    expect(screen.getByRole('button', { name: 'Connect Account' })).toBeDisabled()
    await userEvent.click(setup)
    await userEvent.click(screen.getByRole('button', { name: 'Save fixture credentials' }))
    expect(
      screen.getByText('Credentials saved. Restart OpenAlgo, then connect your broker.')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect Account' })).toBeDisabled()
  })

  it('keeps existing configured broker login available', async () => {
    mockConfig('configured-key')
    render(<BrokerSelect />)
    expect(await screen.findByRole('button', { name: 'Edit broker credentials' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Connect Account' })).toBeEnabled()
  })
})
