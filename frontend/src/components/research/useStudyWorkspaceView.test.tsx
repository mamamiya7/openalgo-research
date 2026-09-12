import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  defaultStudyWorkspaceView,
  readStudyView,
  type StudyWorkspaceView,
  studyWorkspaceIdentity,
  useStudyWorkspaceView,
  writeStudyView,
} from './useStudyWorkspaceView'

const storageKey = 'research-study-navigation-v1'
const key = (owner = 'alice', job = 'job-a', artifact = 'artifact-a') =>
  JSON.stringify([owner, job, artifact])
const savedView = (changes: Partial<StudyWorkspaceView> = {}): StudyWorkspaceView => ({
  surface: 'study',
  section: 'trials',
  trials: {
    scope: 'all',
    sortKey: 'max_drawdown_pct',
    sortDirection: 'asc',
    page: 2,
    selectedConfigId: 'candidate-a',
  },
  candidate: { configId: 'candidate-a', proposalNumber: 12 },
  parameters: ['strategy.target_pct', 'strategy.stop_pct'],
  advanced: true,
  ...changes,
})
beforeEach(() => sessionStorage.clear())
afterEach(() => vi.restoreAllMocks())

describe('tab-local study navigation', () => {
  it('maps exact historical summary aliases to the visible sort column without changing native definitions', () => {
    writeStudyView(
      key(),
      savedView({ trials: { ...savedView().trials, sortKey: 'account_initial_capital' } })
    )
    expect(readStudyView(key()).trials).toMatchObject({
      sortKey: 'initial_capital',
      sortDirection: 'asc',
      page: 2,
    })
    writeStudyView(
      key(),
      savedView({ trials: { ...savedView().trials, sortKey: 'vectorbt_sharpe_ratio' } })
    )
    expect(readStudyView(key()).trials.sortKey).toBe('vectorbt_sharpe_ratio')
  })
  it('isolates experiment context and retains both table scroll axes with the exact trial', () => {
    const first = studyWorkspaceIdentity('alice', 'study', 'artifact', 'idea-one')
    const other = studyWorkspaceIdentity('alice', 'study', 'artifact', 'idea-two')
    const value = savedView({ trials: { ...savedView().trials, scrollTop: 180, scrollLeft: 420 } })
    writeStudyView(first, value)
    expect(readStudyView(first)).toEqual(value)
    expect(readStudyView(other)).toEqual(defaultStudyWorkspaceView)
    expect(readStudyView(studyWorkspaceIdentity('bob', 'study', 'artifact', 'idea-one'))).toEqual(
      defaultStudyWorkspaceView
    )
    expect(studyWorkspaceIdentity('alice', 'job-a', 'artifact-a')).toBe(key())
    for (const invalid of [-1, '100', null, 1000001]) {
      sessionStorage.setItem(
        storageKey,
        JSON.stringify([
          {
            key: first,
            view: {
              ...value,
              trials: { ...value.trials, scrollTop: invalid, scrollLeft: invalid },
            },
          },
        ])
      )
      expect(readStudyView(first).trials.scrollTop).toBeUndefined()
      expect(readStudyView(first).trials.scrollLeft).toBeUndefined()
      expect(readStudyView(first).candidate).toEqual(value.candidate)
    }
  })
  it('restores saved section, candidate, axes and table state without posting defaults or modifying financial evidence', () => {
    const saved = savedView()
    writeStudyView(key(), saved)
    const before = sessionStorage.getItem(storageKey)
    const writes = vi.spyOn(Storage.prototype, 'setItem')
    const { result, unmount } = renderHook(() => useStudyWorkspaceView(key()))
    expect(result.current[0]).toEqual(saved)
    expect(writes).not.toHaveBeenCalled()
    unmount()
    expect(sessionStorage.getItem(storageKey)).toBe(before)
    const reopened = renderHook(() => useStudyWorkspaceView(key()))
    expect(reopened.result.current[0]).toEqual(saved)
  })

  it('isolates owner, job and artifact keys and changes context without showing a previous study candidate', () => {
    const alice = key()
    const bob = key('bob')
    const otherJob = key('alice', 'job-b')
    const otherArtifact = key('alice', 'job-a', 'artifact-b')
    writeStudyView(alice, savedView())
    writeStudyView(
      bob,
      savedView({ section: 'parameters', candidate: { configId: 'bob-only', proposalNumber: 0 } })
    )
    writeStudyView(otherJob, savedView({ section: 'activity', candidate: null }))
    writeStudyView(
      otherArtifact,
      savedView({ section: 'overview', advanced: false, candidate: null })
    )
    const { result, rerender } = renderHook(({ id }) => useStudyWorkspaceView(id), {
      initialProps: { id: alice },
    })
    expect(result.current[0].candidate?.configId).toBe('candidate-a')
    rerender({ id: bob })
    expect(result.current[0]).toMatchObject({
      section: 'parameters',
      candidate: { configId: 'bob-only', proposalNumber: 0 },
    })
    rerender({ id: otherJob })
    expect(result.current[0]).toMatchObject({ section: 'activity', candidate: null })
    rerender({ id: otherArtifact })
    expect(result.current[0]).toMatchObject({
      section: 'overview',
      candidate: null,
      advanced: false,
    })
    act(() => result.current[1](savedView({ section: 'parameters', candidate: null })))
    expect(readStudyView(alice).candidate?.configId).toBe('candidate-a')
    expect(readStudyView(bob).candidate?.configId).toBe('bob-only')
    expect(readStudyView(otherArtifact).section).toBe('parameters')
    rerender({ id: key('alice', 'new-job') })
    expect(result.current[0]).toEqual(defaultStudyWorkspaceView)
  })

  it('keeps only the most recently saved 20 study contexts and updates an existing key without duplication', () => {
    for (let index = 0; index < 23; index++) writeStudyView(`study-${index}`, savedView())
    let entries = JSON.parse(sessionStorage.getItem(storageKey)!) as Array<{ key: string }>
    expect(entries).toHaveLength(20)
    expect(entries[0].key).toBe('study-3')
    expect(readStudyView('study-0')).toEqual(defaultStudyWorkspaceView)
    writeStudyView('study-3', savedView({ section: 'parameters' }))
    entries = JSON.parse(sessionStorage.getItem(storageKey)!)
    expect(entries).toHaveLength(20)
    expect(entries.filter((item) => item.key === 'study-3')).toHaveLength(1)
    expect(entries[19].key).toBe('study-3')
    writeStudyView('study-23', savedView())
    expect(readStudyView('study-4')).toEqual(defaultStudyWorkspaceView)
    expect(readStudyView('study-3').section).toBe('parameters')
  })

  it('ignores malformed storage and rejects invalid navigation rather than trusting stale browser state', () => {
    for (const text of ['not-json', 'null', '{}', '42', '[null, false, 3, "text"]']) {
      sessionStorage.setItem(storageKey, text)
      expect(readStudyView(key())).toEqual(defaultStudyWorkspaceView)
    }
    for (const value of [
      null,
      {},
      { section: 'unknown' },
      savedView({ trials: null as unknown as StudyWorkspaceView['trials'] }),
      { ...savedView(), trials: { ...savedView().trials, scope: 'unknown' } },
      { ...savedView(), trials: { ...savedView().trials, sortDirection: 'wrong' } },
      { ...savedView(), trials: { ...savedView().trials, sortKey: 1 } },
      { ...savedView(), trials: { ...savedView().trials, sortKey: '' } },
      { ...savedView(), trials: { ...savedView().trials, sortKey: 'x'.repeat(129) } },
      ...[-1, 1.5, 1001, '2'].map((page) => ({
        ...savedView(),
        trials: { ...savedView().trials, page },
      })),
    ]) {
      sessionStorage.setItem(storageKey, JSON.stringify([{ key: key(), view: value }]))
      expect(readStudyView(key())).toEqual(defaultStudyWorkspaceView)
    }
  })

  it('sanitizes optional candidate and parameter fields while retaining valid zero-based trial identity', () => {
    const receipt = {
      ...savedView(),
      candidate: { configId: 'candidate-a', proposalNumber: 0 },
      parameters: [5, 'first', null, 'second', 'third'],
      advanced: 'true',
    }
    sessionStorage.setItem(storageKey, JSON.stringify([{ key: key(), view: receipt }]))
    expect(readStudyView(key())).toMatchObject({
      candidate: { configId: 'candidate-a', proposalNumber: 0 },
      parameters: ['first', 'second'],
      advanced: false,
    })
    for (const candidate of [
      null,
      {},
      'old',
      { configId: 12, proposalNumber: 0 },
      { configId: 'a', proposalNumber: 0.5 },
      { configId: 'a', proposalNumber: -1 },
      { configId: 'a', proposalNumber: Number.MAX_SAFE_INTEGER + 1 },
      { configId: '', proposalNumber: 0 },
      { configId: 'x'.repeat(129), proposalNumber: 0 },
    ]) {
      sessionStorage.setItem(
        storageKey,
        JSON.stringify([{ key: key(), view: { ...savedView(), candidate } }])
      )
      expect(readStudyView(key()).candidate).toBeNull()
    }
    sessionStorage.setItem(
      storageKey,
      JSON.stringify([
        {
          key: key(),
          view: {
            ...savedView(),
            trials: { ...savedView().trials, selectedConfigId: { wrong: true } },
          },
        },
      ])
    )
    expect(readStudyView(key()).trials.selectedConfigId).toBeNull()
  })

  it('bounds reads to the latest 20 entries even when the stored list was oversized', () => {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify(
        Array.from({ length: 25 }, (_, index) => ({ key: `study-${index}`, view: savedView() }))
      )
    )
    expect(readStudyView('study-4')).toEqual(defaultStudyWorkspaceView)
    expect(readStudyView('study-5')).toEqual(savedView())
    expect(readStudyView('study-24')).toEqual(savedView())
  })

  it('retains the report surface and normalizes older or invalid surface values to the study', () => {
    writeStudyView(key(), savedView({ surface: 'report' }))
    expect(readStudyView(key()).surface).toBe('report')
    for (const surface of [undefined, null, 'unknown', 42]) {
      sessionStorage.setItem(
        storageKey,
        JSON.stringify([{ key: key(), view: { ...savedView(), surface } }])
      )
      expect(readStudyView(key()).surface).toBe('study')
    }
  })

  it('keeps browsing usable in memory when reading or writing session storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Storage unavailable')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage unavailable')
    })
    const { result, rerender } = renderHook(({ id }) => useStudyWorkspaceView(id), {
      initialProps: { id: key() },
    })
    expect(result.current[0]).toEqual(defaultStudyWorkspaceView)
    act(() => result.current[1](savedView()))
    expect(result.current[0]).toEqual(savedView())
    rerender({ id: key('bob') })
    expect(result.current[0]).toEqual(defaultStudyWorkspaceView)
    expect(() => writeStudyView('test', savedView())).not.toThrow()
  })
})
