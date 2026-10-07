import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChartinkConnect } from './ChartinkConnect'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Chartink installation journey', () => {
  it('offers the extension, copies this installation address and returns to the opener', async () => {
    const user = userEvent.setup()
    const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    render(<ChartinkConnect />)
    const opener = screen.getByRole('button', { name: 'Import from Chartink' })
    await user.click(opener)
    const dialog = within(screen.getByRole('dialog', { name: 'Chartink to OpenAlgo' }))
    expect(dialog.getByRole('link', { name: 'Download extension' })).toHaveAttribute(
      'href',
      'https://github.com/mamamiya7/openalgo-research/releases/download/chartink-v0.1.2/openalgo-chartink-0.1.2.zip'
    )
    expect(dialog.getByRole('textbox', { name: 'Your OpenAlgo address' })).toHaveValue(
      window.location.origin
    )
    await user.click(dialog.getByRole('button', { name: 'Copy OpenAlgo address' }))
    expect(copy).toHaveBeenCalledWith(window.location.origin)
    expect(dialog.getByRole('status')).toHaveTextContent('Address copied.')
    expect(dialog.getByRole('link', { name: 'Open Chartink' })).toHaveAttribute(
      'href',
      'https://chartink.com/screeners'
    )
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })

  it('keeps the address selectable when clipboard access is denied', async () => {
    const user = userEvent.setup()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('Permission denied'))
    render(<ChartinkConnect />)
    await user.click(screen.getByRole('button', { name: 'Import from Chartink' }))
    await user.click(screen.getByRole('button', { name: 'Copy OpenAlgo address' }))
    expect(screen.getByRole('status')).toHaveTextContent('copy it manually')
    expect(screen.getByRole('textbox', { name: 'Your OpenAlgo address' })).toHaveValue(
      window.location.origin
    )
  })
})
