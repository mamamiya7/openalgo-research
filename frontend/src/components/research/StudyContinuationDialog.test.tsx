import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  StudyContinuationDialog,
  type StudyContinuationDialogProps,
} from './StudyContinuationDialog'

afterEach(cleanup)

function mount(overrides: Partial<StudyContinuationDialogProps> = {}) {
  const props: StudyContinuationDialogProps = {
    open: true,
    onOpenChange: vi.fn(),
    currentProposed: 100,
    studyName: 'Momentum signals',
    onConfirm: vi.fn(),
    ...overrides,
  }
  return { ...render(<StudyContinuationDialog {...props} />), props }
}

describe('explicit study continuation', () => {
  it('shows the bounded added budget and only submits on confirmation', async () => {
    const user = userEvent.setup()
    const { props } = mount()
    expect(screen.getByRole('dialog', { name: 'Add trials' })).toBeVisible()
    expect(screen.getByText('100 existing trials')).toBeVisible()
    expect(screen.getByText('125')).toBeVisible()
    expect(screen.getByText(/same prices and search ranges/)).toBeVisible()
    expect(screen.getByRole('spinbutton', { name: 'Additional trials' })).toHaveValue(25)
    expect(props.onConfirm).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Add trials' }))
    expect(props.onConfirm).toHaveBeenCalledExactlyOnceWith(25)
  })

  it('focuses the input and allows keyboard confirmation', async () => {
    const user = userEvent.setup()
    const { props } = mount()
    const input = screen.getByRole('spinbutton', { name: 'Additional trials' })
    expect(input).toHaveFocus()
    await user.clear(input)
    await user.type(input, '40{Enter}')
    expect(props.onConfirm).toHaveBeenCalledExactlyOnceWith(40)
  })

  it.each([
    '',
    '0',
    '-1',
    '2.5',
    '901',
    '9999999999999999999999',
  ])('does not admit an invalid or unbounded additional budget: %s', (value) => {
    const { props } = mount()
    const input = screen.getByRole('spinbutton', { name: 'Additional trials' })
    fireEvent.change(input, { target: { value } })
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Choose 1–900 additional trials.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Add trials' })).toBeDisabled()
    fireEvent.submit(input.closest('form')!)
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it('caps the default to the remaining permitted proposals and never raises the hard limit', () => {
    const { props } = mount({ currentProposed: 997, maxTotal: 5000 })
    const input = screen.getByRole('spinbutton', { name: 'Additional trials' })
    expect(input).toHaveValue(3)
    expect(input).toHaveAttribute('max', '3')
    expect(screen.getByText('1,000')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Add trials' }))
    expect(props.onConfirm).toHaveBeenCalledExactlyOnceWith(3)
  })

  it('honours a lower study-specific limit', () => {
    mount({ currentProposed: 190, maxTotal: 200 })
    expect(screen.getByRole('spinbutton', { name: 'Additional trials' })).toHaveValue(10)
    expect(screen.getByText('200')).toBeVisible()
  })

  it.each([
    1000, 1001,
  ])('keeps a study at its limit %s from starting more work', (currentProposed) => {
    const { props } = mount({ currentProposed })
    expect(screen.getByText('This study has reached its 1,000-trial limit.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Add trials' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it('rejects unavailable totals instead of presenting a fabricated budget', () => {
    mount({ currentProposed: Number.NaN })
    expect(
      screen.getByText('Study totals are unavailable. Reopen the study to continue.')
    ).toBeVisible()
    expect(screen.queryByText(/existing trials/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add trials' })).toBeDisabled()
  })

  it('prevents repeat submission while pending and delegates dismissal for parent abort', async () => {
    const user = userEvent.setup()
    const { props } = mount({ pending: true })
    expect(screen.getByRole('button', { name: 'Starting…' })).toBeDisabled()
    fireEvent.submit(screen.getByRole('spinbutton').closest('form')!)
    expect(props.onConfirm).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(props.onOpenChange).toHaveBeenCalledWith(false)
    await user.keyboard('{Escape}')
    expect(props.onOpenChange).toHaveBeenCalledTimes(2)
  })

  it('retains the entered amount after a rejected request and announces the error', () => {
    const { props, rerender } = mount()
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '50' } })
    rerender(<StudyContinuationDialog {...props} error="The study has changed. Reopen it." />)
    expect(screen.getByRole('spinbutton')).toHaveValue(50)
    expect(screen.getByRole('alert')).toHaveTextContent('The study has changed. Reopen it.')
  })

  it('starts each reopened dialog with the current bounded default', () => {
    const { props, rerender } = mount()
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '50' } })
    rerender(<StudyContinuationDialog {...props} open={false} />)
    rerender(<StudyContinuationDialog {...props} />)
    expect(screen.getByRole('spinbutton')).toHaveValue(25)
  })

  it('reopens an uncertain request with its original amount instead of the default', () => {
    const { props, rerender } = mount({ initialAdditionalTrials: 75 })
    expect(screen.getByRole('spinbutton')).toHaveValue(75)
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '10' } })
    rerender(<StudyContinuationDialog {...props} open={false} />)
    rerender(<StudyContinuationDialog {...props} />)
    expect(screen.getByRole('spinbutton')).toHaveValue(75)
    fireEvent.click(screen.getByRole('button', { name: 'Add trials' }))
    expect(props.onConfirm).toHaveBeenCalledExactlyOnceWith(75)
  })

  it('locks the exact amount while recovering a previous request', async () => {
    const user = userEvent.setup()
    const { props } = mount({ initialAdditionalTrials: 75, amountLocked: true })
    const input = screen.getByRole('spinbutton')
    expect(input).toHaveAttribute('readonly')
    fireEvent.change(input, { target: { value: '10' } })
    expect(input).toHaveValue(75)
    await user.keyboard('{Enter}')
    expect(props.onConfirm).toHaveBeenCalledExactlyOnceWith(75)
  })

  it('has an accessible named dialog and keyboard focus stays inside it', async () => {
    const user = userEvent.setup()
    mount()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Add trials' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('spinbutton')).toHaveFocus()
    expect((await axe(document.body)).violations).toEqual([])
  })
})
