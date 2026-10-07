import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BrokerCredentialsSetup } from './BrokerCredentialsSetup'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient: { get, post } }))

const config = {
  current_broker: 'fyers',
  valid_brokers: ['fyers', 'zerodha'],
  redirect_url: 'http://127.0.0.1:5247/fyers/callback',
  broker_api_key_raw_length: 0,
}

describe('BrokerCredentialsSetup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    get.mockResolvedValue({ data: { status: 'success', data: config } })
    post.mockResolvedValue({ data: { status: 'success', restart_required: true } })
  })

  it('shows the configured callback and saves only native broker fields', async () => {
    const onSaved = vi.fn()
    render(<BrokerCredentialsSetup open onOpenChange={vi.fn()} onSaved={onSaved} />)
    const key = await screen.findByLabelText('Broker API key')
    expect(screen.getByLabelText('Callback URL')).toHaveValue(config.redirect_url)
    expect(screen.getByRole('button', { name: 'Save broker credentials' })).toBeDisabled()
    await userEvent.type(key, 'fixture-key')
    await userEvent.type(screen.getByLabelText('Broker API secret'), 'fixture-secret')
    await userEvent.click(screen.getByRole('button', { name: 'Save broker credentials' }))
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/api/broker/credentials',
        {
          broker_api_key: 'fixture-key',
          broker_api_secret: 'fixture-secret',
          redirect_url: config.redirect_url,
        },
        { timeout: 30000 }
      )
    )
    expect(onSaved).toHaveBeenCalledOnce()
    expect(screen.getByRole('status')).toHaveTextContent('Restart OpenAlgo')
    expect(screen.queryByLabelText('Broker API key')).not.toBeInTheDocument()
  })

  it('keeps broker fixed when credentials already exist and preserves empty fields', async () => {
    get.mockResolvedValue({
      data: { status: 'success', data: { ...config, broker_api_key_raw_length: 12 } },
    })
    render(<BrokerCredentialsSetup open onOpenChange={vi.fn()} onSaved={vi.fn()} />)
    await screen.findByLabelText('Broker API key')
    expect(screen.getByRole('combobox', { name: 'Broker' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Broker API secret'), 'updated-secret')
    await userEvent.click(screen.getByRole('button', { name: 'Save broker credentials' }))
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/api/broker/credentials',
        {
          broker_api_secret: 'updated-secret',
          redirect_url: config.redirect_url,
        },
        { timeout: 30000 }
      )
    )
  })

  it('shows the native credential validation error without claiming success', async () => {
    post.mockRejectedValue({ response: { data: { message: 'Invalid broker API key format.' } } })
    const onSaved = vi.fn()
    render(<BrokerCredentialsSetup open onOpenChange={vi.fn()} onSaved={onSaved} />)
    await userEvent.type(await screen.findByLabelText('Broker API key'), 'fixture-key')
    await userEvent.click(screen.getByRole('button', { name: 'Save broker credentials' }))
    expect(await screen.findByText('Invalid broker API key format.')).toBeInTheDocument()
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('does not fetch credentials until opened and cancels its request on close', async () => {
    const props = { onOpenChange: vi.fn(), onSaved: vi.fn() }
    const view = render(<BrokerCredentialsSetup open={false} {...props} />)
    expect(get).not.toHaveBeenCalled()
    view.rerender(<BrokerCredentialsSetup open {...props} />)
    await screen.findByLabelText('Broker API key')
    const signal = get.mock.calls[0][1].signal as AbortSignal
    view.rerender(<BrokerCredentialsSetup open={false} {...props} />)
    expect(signal.aborted).toBe(true)
  })
})
