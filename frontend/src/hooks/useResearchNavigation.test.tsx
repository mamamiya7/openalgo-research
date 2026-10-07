import { act, fireEvent, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { MemoryRouter, useNavigate, useSearchParams } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  researchBackParams,
  researchNavigationReturn,
  researchReturnLocation,
  useResearchNavigation,
} from './useResearchNavigation'

const storageKey = 'research-page-navigation-v1'
it('accepts a bounded return to the same idea and rejects a foreign or ambiguous idea', () => {
  expect(researchBackParams('experiment=idea&view=studies&job=one', 'idea')?.get('job')).toBe('one')
  expect(researchBackParams('experiment=someone-else&view=studies', 'idea')).toBeNull()
  expect(researchBackParams('experiment=idea&experiment=someone-else', 'idea')).toBeNull()
  expect(researchBackParams(`experiment=idea&q=${'x'.repeat(4100)}`, 'idea')).toBeNull()
})
it('retains a valid review return chain and drops invalid or oversized nested destinations', () => {
  const comparison = 'experiment=idea&view=comparisons&comparison=one'
  const later = new URLSearchParams({
    experiment: 'idea',
    view: 'backtests',
    job: 'later',
    return_research: comparison,
  })
  const history = new URLSearchParams({
    experiment: 'idea',
    view: 'decisions',
    return_research: researchReturnLocation(later, 'idea'),
  })
  const back = researchBackParams(
    new URLSearchParams(researchReturnLocation(history, 'idea')).get('return_research'),
    'idea'
  )!
  expect(back.get('job')).toBe('later')
  expect(back.get('return_research')).toBe(comparison)
  for (const invalid of ['old', 'experiment=foreign', `experiment=idea&q=${'x'.repeat(4050)}`]) {
    later.set('return_research', invalid)
    const saved = new URLSearchParams(researchReturnLocation(later, 'idea'))
    expect(saved.get('job')).toBe('later')
    expect(saved.has('return_research')).toBe(false)
  }
})
let height = 5000
let resize: (() => void) | undefined
const disconnect = vi.fn()
const study = 'experiment=idea&view=studies&job=study'
const report = 'experiment=idea&view=backtests&job=candidate&return_job=study'
function Harness({ owner = 'alice', ready = true, enabled = true }) {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const targetRef = useRef<HTMLDivElement>(null)
  useResearchNavigation({
    owner,
    experimentId: params.get('experiment'),
    ready,
    targetRef,
    enabled,
  })
  return (
    <div ref={targetRef}>
      {ready ? (
        <h2 tabIndex={-1} data-research-navigation-heading>
          Result identity
        </h2>
      ) : (
        <p>Loading</p>
      )}
      <output>{params.toString()}</output>
      <button type="button" onClick={() => setParams(report)}>
        Open report
      </button>
      <button type="button" onClick={() => setParams(study)}>
        Open study afresh
      </button>
      <button
        type="button"
        onClick={() => {
          const next = new URLSearchParams(study)
          setParams(next, { state: researchNavigationReturn(owner, 'idea', next) })
        }}
      >
        Return to study
      </button>
      <button type="button" onClick={() => navigate(-1)}>
        Browser back
      </button>
      <button type="button" onClick={() => navigate(1)}>
        Browser forward
      </button>
      <button
        type="button"
        onClick={() => {
          const next = new URLSearchParams(params)
          next.set('q', 'momentum')
          setParams(next, { replace: true })
        }}
      >
        Filter the page
      </button>
      <button
        type="button"
        onClick={() => {
          const next = new URLSearchParams(params)
          next.set('shortlist', 'candidate-one')
          setParams(next)
        }}
      >
        Inspect a shortlist row
      </button>
    </div>
  )
}
function mount(options: { owner?: string; ready?: boolean; enabled?: boolean } = {}) {
  const tree = (props = options) => (
    <MemoryRouter initialEntries={[`/scanner-research?${study}`]}>
      <Harness {...props} />
    </MemoryRouter>
  )
  const result = render(tree())
  return { ...result, update: (props: typeof options) => result.rerender(tree(props)) }
}
function frame() {
  act(() => vi.advanceTimersByTime(20))
}
function scroll(y: number) {
  act(() => {
    Object.defineProperty(window, 'scrollY', { configurable: true, value: y })
    window.dispatchEvent(new Event('scroll'))
  })
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  sessionStorage.clear()
  height = 5000
  resize = undefined
  disconnect.mockClear()
  Object.defineProperty(document.documentElement, 'scrollHeight', {
    configurable: true,
    get: () => height,
  })
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 600 })
  Object.defineProperty(window, 'scrollX', { configurable: true, value: 0 })
  Object.defineProperty(window, 'scrollY', { configurable: true, value: 0 })
  window.history.scrollRestoration = 'auto'
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    () => ({ top: 300 - window.scrollY }) as DOMRect
  )
  vi.spyOn(window, 'scrollTo').mockImplementation((options) => {
    if (typeof options !== 'object') return
    Object.defineProperty(window, 'scrollY', { configurable: true, value: options.top ?? 0 })
    Object.defineProperty(window, 'scrollX', { configurable: true, value: options.left ?? 0 })
    window.dispatchEvent(new Event('scroll'))
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: () => void) {
        resize = callback
      }
      observe() {}
      disconnect() {
        disconnect()
      }
    }
  )
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('research route navigation', () => {
  it.each([
    'sticky',
    'fixed',
  ])('keeps a new report below a measured %s top navigation without shifting an exact Back position', (position) => {
    let navigationHeight = 56
    render(
      <nav aria-label="Primary" style={{ position: position as 'sticky' | 'fixed', top: 0 }}>
        OpenAlgo
      </nav>
    )
    vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockImplementation(function (
      this: HTMLElement
    ) {
      return this.getAttribute('aria-label') === 'Primary'
        ? ({ top: 0, bottom: navigationHeight, height: navigationHeight, width: 1200 } as DOMRect)
        : ({ top: 300 - window.scrollY } as DOMRect)
    })
    mount()
    frame()
    scroll(808)
    fireEvent.click(screen.getByRole('button', { name: 'Open report' }))
    frame()
    expect(screen.getByRole('heading').getBoundingClientRect().top).toBe(72)
    expect(screen.getByRole('heading')).toHaveFocus()
    navigationHeight = 88
    fireEvent.click(screen.getByRole('button', { name: 'Return to study' }))
    frame()
    expect(window.scrollY).toBe(808)
    fireEvent.click(screen.getByRole('button', { name: 'Open report' }))
    frame()
    expect(screen.getByRole('heading').getBoundingClientRect().top).toBe(104)
  })
  it('preserves the current page position when URL filters or an in-place shortlist dialog change', () => {
    mount()
    frame()
    scroll(970)
    fireEvent.click(screen.getByRole('button', { name: 'Filter the page' }))
    frame()
    expect(window.scrollY).toBe(970)
    fireEvent.click(screen.getByRole('button', { name: 'Inspect a shortlist row' }))
    frame()
    expect(window.scrollY).toBe(970)
    fireEvent.click(screen.getByRole('button', { name: 'Browser back' }))
    frame()
    expect(window.scrollY).toBe(970)
  })
  it('opens the identity on forward navigation and restores each history entry on browser back and forward', () => {
    mount()
    frame()
    expect(window.scrollY).toBe(284)
    expect(screen.getByRole('heading')).toHaveFocus()
    scroll(1300)
    fireEvent.click(screen.getByRole('button', { name: 'Open report' }))
    frame()
    expect(window.scrollY).toBe(284)
    scroll(740)
    fireEvent.click(screen.getByRole('button', { name: 'Browser back' }))
    frame()
    expect(window.scrollY).toBe(1300)
    fireEvent.click(screen.getByRole('button', { name: 'Browser forward' }))
    frame()
    expect(window.scrollY).toBe(740)
  })

  it('restores an explicitly marked return but starts a fresh click at the identity', () => {
    mount()
    frame()
    scroll(1100)
    fireEvent.click(screen.getByRole('button', { name: 'Open report' }))
    frame()
    fireEvent.click(screen.getByRole('button', { name: 'Return to study' }))
    frame()
    expect(window.scrollY).toBe(1100)
    fireEvent.click(screen.getByRole('button', { name: 'Open report' }))
    frame()
    fireEvent.click(screen.getByRole('button', { name: 'Open study afresh' }))
    frame()
    expect(window.scrollY).toBe(284)
  })

  it('waits for the actual destination and restores delayed chart height without reacting to query refetches', () => {
    const app = mount()
    frame()
    scroll(1700)
    fireEvent.click(screen.getByRole('button', { name: 'Open report' }))
    frame()
    app.update({ ready: false })
    height = 700
    vi.mocked(window.scrollTo).mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Return to study' }))
    frame()
    expect(window.scrollTo).not.toHaveBeenCalled()
    app.update({ ready: true })
    frame()
    expect(window.scrollY).toBe(100)
    height = 5000
    act(() => resize?.())
    frame()
    expect(window.scrollY).toBe(1700)
    vi.mocked(window.scrollTo).mockClear()
    app.update({ ready: true })
    frame()
    expect(window.scrollTo).not.toHaveBeenCalled()
    expect(disconnect).toHaveBeenCalled()
  })

  it('does not restore another account and rejects a return marker belonging to an old owner', () => {
    const app = mount()
    frame()
    scroll(1700)
    fireEvent.click(screen.getByRole('button', { name: 'Open report' }))
    frame()
    fireEvent.click(screen.getByRole('button', { name: 'Return to study' }))
    frame()
    expect(window.scrollY).toBe(1700)
    app.update({ owner: 'bob' })
    frame()
    expect(window.scrollY).toBe(284)
    scroll(410)
    const saved = JSON.parse(sessionStorage.getItem(storageKey)!)
    expect(
      saved.find(
        (item: { destination: string; position: { y: number } }) =>
          item.destination.includes('alice') && item.position.y === 1700
      )
    ).toBeTruthy()
  })

  it('abandons a pending restoration when the user interacts and releases its timer and observers on unmount', () => {
    const app = mount()
    frame()
    scroll(1700)
    fireEvent.click(screen.getByRole('button', { name: 'Open report' }))
    frame()
    height = 700
    fireEvent.click(screen.getByRole('button', { name: 'Return to study' }))
    frame()
    expect(window.scrollY).toBe(100)
    fireEvent.wheel(window)
    vi.mocked(window.scrollTo).mockClear()
    height = 5000
    act(() => resize?.())
    act(() => vi.advanceTimersByTime(3000))
    expect(window.scrollTo).not.toHaveBeenCalled()
    app.unmount()
    expect(window.history.scrollRestoration).toBe('auto')
    expect(vi.getTimerCount()).toBe(0)
    expect(disconnect).toHaveBeenCalled()
  })

  it('bounds saved history, ignores malformed storage, and remains usable when storage is disabled', () => {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify([null, { entry: 'bad', destination: 'bad', position: { x: -1, y: 'wrong' } }])
    )
    const app = mount()
    frame()
    expect(window.scrollY).toBe(284)
    for (let index = 0; index < 65; index++) {
      fireEvent.click(
        screen.getByRole('button', { name: index % 2 ? 'Open report' : 'Open study afresh' })
      )
      frame()
    }
    expect(JSON.parse(sessionStorage.getItem(storageKey)!)).toHaveLength(60)
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Unavailable')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Unavailable')
    })
    expect(() => fireEvent.click(screen.getByRole('button', { name: 'Open report' }))).not.toThrow()
    frame()
    expect(window.scrollY).toBe(284)
    app.unmount()
  })

  it('does not take ownership when a parent owns navigation', () => {
    mount({ enabled: false })
    frame()
    expect(window.scrollTo).not.toHaveBeenCalled()
    expect(window.history.scrollRestoration).toBe('auto')
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })

  it('cannot scroll a new account from an abandoned delayed observer', () => {
    const app = mount()
    frame()
    scroll(1700)
    fireEvent.click(screen.getByRole('button', { name: 'Open report' }))
    frame()
    height = 700
    fireEvent.click(screen.getByRole('button', { name: 'Return to study' }))
    frame()
    const abandonedResize = resize
    app.update({ owner: 'bob', ready: false })
    vi.mocked(window.scrollTo).mockClear()
    height = 5000
    act(() => abandonedResize?.())
    act(() => vi.advanceTimersByTime(3000))
    expect(window.scrollTo).not.toHaveBeenCalled()
    app.update({ owner: 'bob', ready: true })
    frame()
    expect(window.scrollY).toBe(284)
  })
})
