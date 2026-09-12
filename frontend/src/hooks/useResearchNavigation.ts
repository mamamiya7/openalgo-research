import { type RefObject, useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigationType } from 'react-router'

type Position = { x: number; y: number }
export function researchBackParams(
  raw: string | null,
  experimentId: string
): URLSearchParams | null {
  if (!raw || raw.length > 4096) return null
  const params = new URLSearchParams(raw)
  return params.getAll('experiment').length === 1 && params.get('experiment') === experimentId
    ? params
    : null
}
/** Preserve the previous review destination while keeping the return chain bounded. */
export function researchReturnLocation(params: URLSearchParams, experimentId: string): string {
  const back = new URLSearchParams(params)
  if (
    back.getAll('return_research').length !== 1 ||
    !researchBackParams(back.get('return_research'), experimentId)
  )
    back.delete('return_research')
  if (back.toString().length > 4096) back.delete('return_research')
  return back.toString()
}

type Entry = { entry: string; destination: string; position: Position }
const storageKey = 'research-page-navigation-v1'
const limit = 60
const coordinate = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1000000
const searchKey = (search: string | URLSearchParams) => {
  const params = new URLSearchParams(search)
  params.sort()
  return params.toString()
}
function topNavigationInset() {
  let inset = 0
  for (const element of document.querySelectorAll<HTMLElement>(
    'header, nav, [role="banner"], [role="navigation"]'
  )) {
    const style = window.getComputedStyle(element)
    const top = Number.parseFloat(style.top)
    if (
      !['sticky', 'fixed'].includes(style.position) ||
      !Number.isFinite(top) ||
      top < 0 ||
      style.visibility === 'hidden' ||
      style.display === 'none'
    )
      continue
    const { height } = element.getBoundingClientRect()
    if (height > 0) inset = Math.max(inset, top + height)
  }
  return inset
}
function entries(): Entry[] {
  try {
    const serialized = sessionStorage.getItem(storageKey) ?? '[]'
    if (serialized.length > 150000) return []
    const saved: unknown = JSON.parse(serialized)
    if (!Array.isArray(saved)) return []
    return saved
      .slice(-limit)
      .filter(
        (item): item is Entry =>
          item &&
          typeof item.entry === 'string' &&
          item.entry.length < 3000 &&
          typeof item.destination === 'string' &&
          item.destination.length < 3000 &&
          coordinate(item.position?.x) &&
          coordinate(item.position?.y)
      )
  } catch {
    return []
  }
}
function save(entry: Entry) {
  try {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify(
        [...entries().filter((item) => item.entry !== entry.entry), entry].slice(-limit)
      )
    )
  } catch {
    // Navigation is usable without browser storage.
  }
}

/** Explicit Back actions may restore this exact destination; ordinary tab clicks never do. */
export function researchNavigationReturn(
  owner: string,
  experimentId: string | null | undefined,
  params: URLSearchParams
) {
  return {
    researchNavigationReturn: {
      owner,
      experimentId: experimentId ?? null,
      search: searchKey(params),
    },
  }
}

interface Options {
  owner: string
  experimentId?: string | null
  ready: boolean
  targetRef: RefObject<HTMLElement | null>
  enabled?: boolean
}
interface Visit extends Entry {
  page: string
  pending: boolean
  restore: boolean
  touched: boolean
}

/** One route owner manages scrolling. Result data refetches do not create a new visit. */
export function useResearchNavigation({
  owner,
  experimentId,
  ready,
  targetRef,
  enabled = true,
}: Options) {
  const location = useLocation()
  const navigationType = useNavigationType()
  const search = searchKey(location.search)
  const destination = JSON.stringify([owner, experimentId ?? null, location.pathname, search])
  const entry = JSON.stringify([destination, location.key])
  const visit = useRef<Visit | null>(null)
  const lastVisit = useRef<Visit | null>(null)
  // Filters, selected shortlist rows and comparison checkbox state belong to the
  // current page. Their URL updates must not move a trader away from that control.
  const pageParams = new URLSearchParams(search)
  const page = JSON.stringify([
    owner,
    experimentId ?? null,
    location.pathname,
    ...[
      'view',
      'job',
      'report',
      'library',
      'chartink_import',
      'legacy',
      'version',
      'comparison',
      'comparison_member',
      'decision',
      'decision_event',
      'decision_report',
    ].map((key) => [key, pageParams.get(key)]),
  ])
  const requestedReturn = location.state?.researchNavigationReturn
  const explicitReturn = Boolean(
    requestedReturn &&
      requestedReturn.owner === owner &&
      requestedReturn.experimentId === (experimentId ?? null) &&
      requestedReturn.search === search
  )

  useLayoutEffect(() => {
    if (!enabled) return
    const saved = entries()
    const previous =
      navigationType === 'POP'
        ? saved.find((item) => item.entry === entry)
        : explicitReturn
          ? [...saved].reverse().find((item) => item.destination === destination)
          : undefined
    const samePage =
      navigationType !== 'POP' && lastVisit.current?.page === page ? lastVisit.current : null
    const current: Visit = {
      entry,
      destination,
      page,
      position: previous?.position ?? samePage?.position ?? { x: 0, y: 0 },
      pending: true,
      restore: Boolean(previous || samePage),
      touched: false,
    }
    visit.current = current
    const priorRestoration = window.history.scrollRestoration
    window.history.scrollRestoration = 'manual'
    let timer: ReturnType<typeof setTimeout> | undefined
    const flush = () => {
      if (timer) clearTimeout(timer)
      timer = undefined
      if (!current.pending || current.touched) save(current)
    }
    const record = () => {
      if (current.pending) return
      current.position = {
        x: Math.max(0, window.scrollX),
        y: Math.max(0, window.scrollY),
      }
      current.touched = true
      if (!timer) timer = setTimeout(flush, 120)
    }
    window.addEventListener('scroll', record, { passive: true })
    window.addEventListener('pagehide', flush)
    return () => {
      flush()
      lastVisit.current = current
      window.removeEventListener('scroll', record)
      window.removeEventListener('pagehide', flush)
      window.history.scrollRestoration = priorRestoration
      if (visit.current === current) visit.current = null
    }
  }, [entry, destination, page, enabled, explicitReturn, navigationType])

  useLayoutEffect(() => {
    const current = visit.current
    const container = targetRef.current
    if (!enabled || !ready || !container || !current?.pending || current.entry !== entry) return
    let observer: ResizeObserver | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    let frame: number | undefined
    let disposed = false
    const finish = () => {
      current.pending = false
      current.position = { x: Math.max(0, window.scrollX), y: Math.max(0, window.scrollY) }
      save(current)
      observer?.disconnect()
      if (timer) clearTimeout(timer)
      if (frame !== undefined) cancelAnimationFrame(frame)
      window.removeEventListener('wheel', interrupt)
      window.removeEventListener('touchstart', interrupt)
      window.removeEventListener('keydown', interrupt)
      window.removeEventListener('pointerdown', interrupt)
    }
    const interrupt = () => finish()
    const heading = container.querySelector<HTMLElement>('[data-research-navigation-heading]')
    const desired = current.restore
      ? current.position
      : {
          x: 0,
          y: Math.max(
            0,
            (heading ?? container).getBoundingClientRect().top +
              window.scrollY -
              topNavigationInset() -
              16
          ),
        }
    const apply = () => {
      if (disposed || !current.pending) return
      const maximum = Math.max(0, document.documentElement.scrollHeight - window.innerHeight)
      const top = Math.min(desired.y, maximum)
      if (Math.abs(window.scrollY - top) > 1 || Math.abs(window.scrollX - desired.x) > 1)
        window.scrollTo({ left: desired.x, top, behavior: 'instant' })
      if (desired.y <= maximum + 1) {
        if (!current.restore) heading?.focus({ preventScroll: true })
        finish()
      }
    }
    const schedule = () => {
      if (disposed || !current.pending) return
      if (frame !== undefined) cancelAnimationFrame(frame)
      frame = requestAnimationFrame(apply)
    }
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(schedule)
      observer.observe(container)
      observer.observe(document.body)
    }
    window.addEventListener('wheel', interrupt, { passive: true })
    window.addEventListener('touchstart', interrupt, { passive: true })
    window.addEventListener('keydown', interrupt)
    window.addEventListener('pointerdown', interrupt, { passive: true })
    timer = setTimeout(() => {
      apply()
      finish()
    }, 2500)
    // Let mounted dialogs restore their selected trial before positioning the page.
    schedule()
    return () => {
      disposed = true
      observer?.disconnect()
      if (timer) clearTimeout(timer)
      if (frame !== undefined) cancelAnimationFrame(frame)
      window.removeEventListener('wheel', interrupt)
      window.removeEventListener('touchstart', interrupt)
      window.removeEventListener('keydown', interrupt)
      window.removeEventListener('pointerdown', interrupt)
    }
  }, [enabled, ready, entry, targetRef])
}
