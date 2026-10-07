import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  freshDraft,
  operationIdentity,
  readDraft,
  rememberSubmission,
  requestIdentity,
  specification,
  submissionHistory,
  updateSubmissionStatus,
  writeDraft,
} from './researchDraft'

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.restoreAllMocks())
describe('exact research payload identity', () => {
  it('uses VectorBT for fresh backtests and Optuna when those settings are optimized', () => {
    const draft = readDraft('new-account')
    expect(specification(draft)).toEqual({ execution: { engine: 'vectorbt', optimizer: 'native' } })
    draft.kind = 'optimize'
    expect(specification(draft)).toMatchObject({
      execution: { engine: 'vectorbt', optimizer: 'optuna' },
    })
  })
  it('recovers pre-connector drafts with the original engine and all financial requirements', () => {
    const draft = freshDraft()
    draft.execution = undefined
    draft.config.entry_priority = 'shuffle'
    draft.config.priority_seed = 91
    draft.config.max_exposure_pct = 45
    draft.config.trade_horizon = 'intraday'
    draft.config.hold_minutes = 30
    writeDraft('alice', draft)
    expect(JSON.parse(sessionStorage.getItem('research-draft:alice') || '{}')).not.toHaveProperty(
      'execution'
    )
    const reopened = readDraft('alice')
    expect(reopened.config).toEqual(draft.config)
    expect(reopened.execution).toBeUndefined()
    expect(specification(reopened)).toEqual({})
  })
  it('keeps separate fresh runs from sharing mutable signal-filter defaults', () => {
    const draft = freshDraft()
    draft.config.modes.push('Zero Only')
    expect(freshDraft().config.modes).toEqual(['Bypass'])
  })
  it('keeps retries identical when storage reads work but writes fail, including stale stored values', () => {
    const payload = {
      source_id: 'one',
      config: freshDraft().config,
      kind: 'backtest' as const,
      specification: {},
    }
    const stale = requestIdentity('quota-user', payload)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Quota', 'QuotaExceededError')
    })
    const changed = { ...payload, source_id: 'two' }
    const first = requestIdentity('quota-user', changed),
      second = requestIdentity('quota-user', changed)
    expect(first.request_id).not.toBe(stale.request_id)
    expect(second.request_id).toBe(first.request_id)
  })
  it('preserves operation retries under complete storage denial and separates explicit attempts', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Denied')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Denied')
    })
    const first = operationIdentity('denied-user', 'retry', { previous_attempt_id: 'failed' })
    expect(operationIdentity('denied-user', 'retry', { previous_attempt_id: 'failed' })).toBe(first)
    const second = operationIdentity(
      'denied-user',
      'retry',
      { previous_attempt_id: 'failed' },
      true
    )
    expect(second).not.toBe(first)
    expect(operationIdentity('denied-user', 'retry', { previous_attempt_id: 'failed' })).toBe(
      second
    )
  })
  it('remembers terminal submissions without attaching changed inputs to the old attempt', () => {
    const payload = {
      source_id: 'one',
      config: freshDraft().config,
      kind: 'backtest' as const,
      specification: {},
    }
    rememberSubmission('alice', payload, 'job-1', 'queued')
    updateSubmissionStatus('alice', payload, 'job-1', 'failed')
    expect(submissionHistory('alice', payload)).toMatchObject({ job_id: 'job-1', status: 'failed' })
    expect(submissionHistory('alice', { ...payload, source_id: 'changed' })).toBeNull()
    expect(submissionHistory('bob', payload)).toBeNull()
  })
  it('reuses a token after draft reload and object key reordering', () => {
    const draft = freshDraft()
    writeDraft('alice', draft)
    const payload = {
      source_id: 'source-1',
      config: draft.config,
      kind: draft.kind,
      specification: specification(draft),
    }
    const first = requestIdentity('alice', payload)
    const reopened = readDraft('alice')
    const second = requestIdentity('alice', {
      kind: reopened.kind,
      config: reopened.config,
      specification: specification(reopened),
      source_id: 'source-1',
    })
    expect(second.request_id).toBe(first.request_id)
  })
  it('regenerates for changed configuration, source, experiment intent, or specification', () => {
    const draft = freshDraft(),
      base = { source_id: 'one', config: draft.config, kind: draft.kind, specification: {} }
    const original = requestIdentity('alice', base)
    const changes = [
      { ...base, source_id: 'two' },
      { ...base, config: { ...base.config, priority_seed: 10 } },
      { ...base, kind: 'optimize' as const },
      { ...base, specification: { intent: 'select_earlier' } },
    ]
    for (const payload of changes)
      expect(requestIdentity('alice', payload).request_id).not.toBe(original.request_id)
    expect(requestIdentity('bob', base).request_id).not.toBe(original.request_id)
  })
  it('keeps explicit enabled-zero trailing and fixed later intent without a selection search', () => {
    const draft = freshDraft()
    draft.config.trailing_enabled = true
    draft.config.trailing_pct = 0
    draft.kind = 'research'
    writeDraft('alice', draft)
    expect(readDraft('alice').config.trailing_enabled).toBe(true)
    expect(specification(draft)).not.toHaveProperty('search')
    expect(specification(draft)).toHaveProperty('prior_explored', true)
    draft.research.intent = 'select_earlier'
    expect(specification(draft)).toHaveProperty('search', draft.search)
  })
  it('persists large-source draft recovery under a small storage quota without copying or inventing evidence', () => {
    const lineage = Array.from({ length: 12000 }, (_, index) => ({
      date: `receipt-${index}`,
      hash: 'a'.repeat(64),
      description: 'checked official evidence '.repeat(8),
    }))
    const provenance = {
      provider: 'NSE final CM bhavcopy',
      exchange: 'NSE',
      interval: 'D',
      adjustment_basis: 'official-raw-with-reviewed-actions-v1',
      calendar_basis: 'verified-calendar-v1',
      synthetic: false,
      source_receipts: lineage,
      symbol_identities: Object.fromEntries(
        lineage.map((receipt, index) => [`SYMBOL${index}`, receipt])
      ),
    }
    const draft = freshDraft()
    draft.kind = 'research'
    draft.config.priority_seed = 781
    draft.config.trailing_enabled = true
    draft.research.train_end = '2026-01-05'
    draft.research.test_end = '2026-02-05'
    draft.source = {
      id: 'large-immutable-source',
      receipt: {
        input_rows: 18310,
        signal_count: 18310,
        duplicates_removed: 0,
        date_from: '2025-01-01',
        date_to: '2026-02-05',
        symbol_count: 12000,
        warnings: ['Some scanner history predates admitted prices.'],
      },
      provenance,
      coverage: {
        status: 'warning',
        warnings: ['Missing candles remain pending.'],
        provenance,
        symbols: lineage,
      },
    }
    expect(JSON.stringify(draft).length).toBeGreaterThan(5000000)
    const originalSet = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      key: string,
      item: string
    ) {
      if (item.length > 65536) throw new DOMException('Quota exceeded', 'QuotaExceededError')
      originalSet.call(this, key, item)
    })
    const submitted = requestIdentity('alice', {
      source_id: draft.source.id,
      config: draft.config,
      kind: draft.kind,
      specification: specification(draft),
    })
    writeDraft('alice', draft)
    const serialized = sessionStorage.getItem('research-draft:alice')
    expect(serialized).not.toBeNull()
    expect(serialized?.length).toBeLessThan(10000)
    const reopened = readDraft('alice')
    expect(reopened.config).toEqual(draft.config)
    expect(reopened.research).toEqual(draft.research)
    expect(reopened.source?.id).toBe(draft.source.id)
    expect(reopened.source?.coverage.status).toBe('warning')
    expect(reopened.source?.coverage.warnings).toEqual(['Missing candles remain pending.'])
    expect(reopened.source?.coverage.detail_scope).toContain('Compact browser receipt only')
    expect(reopened.source?.coverage).not.toHaveProperty('symbols')
    expect(reopened.source?.coverage).not.toHaveProperty('provenance')
    expect(reopened.source?.provenance).not.toHaveProperty('source_receipts')
    expect(reopened.source?.provenance).not.toHaveProperty('symbol_identities')
    expect(draft.source.provenance).toBe(provenance)
    expect(draft.source.coverage.symbols).toBe(lineage)
    expect(
      requestIdentity('alice', {
        source_id: reopened.source?.id || '',
        config: reopened.config,
        kind: reopened.kind,
        specification: specification(reopened),
      }).request_id
    ).toBe(submitted.request_id)
  })
  it('bounds excessive warning previews and explicitly retains their omitted count', () => {
    const draft = freshDraft()
    draft.source = {
      id: 'warning-source',
      receipt: {
        input_rows: 1,
        signal_count: 1,
        duplicates_removed: 0,
        date_from: '2026-01-01',
        date_to: '2026-01-01',
        symbol_count: 1,
        warnings: [],
      },
      provenance: {
        provider: 'fixture',
        exchange: 'NSE',
        interval: 'D',
        adjustment_basis: 'synthetic',
        calendar_basis: 'weekdays',
        synthetic: true,
      },
      coverage: {
        status: 'blocked',
        warnings: Array.from({ length: 1000 }, () => `Missing record: ${'x'.repeat(2000)}`),
      },
    }
    writeDraft('alice', draft)
    const saved = readDraft('alice')
    expect((saved.source?.coverage.warnings as string[]).length).toBe(25)
    expect(saved.source?.coverage.warnings).toContain(
      '976 additional warnings retained in saved evidence; reopen the source for full coverage.'
    )
    expect(saved.source?.coverage.status).toBe('blocked')
    expect(sessionStorage.getItem('research-draft:alice')?.length).toBeLessThan(40000)
  })
})
