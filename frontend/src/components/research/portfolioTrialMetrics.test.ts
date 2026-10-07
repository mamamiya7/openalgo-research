import { afterEach, describe, expect, it } from 'vitest'
import type { AnalysisMetric, PortfolioTrial } from '@/api/portfolioResearch'
import { availableTrialMetrics, readTrialColumns, trialMetricValue } from './portfolioTrialMetrics'

const catalog: AnalysisMetric[] = [
  {
    key: 'account_initial_capital',
    label: 'Starting capital',
    format: 'money',
    group: 'Account',
    source: 'Shared account',
    description: 'Original saved summary.',
  },
  {
    key: 'native_starting_capital',
    label: 'Starting capital',
    format: 'money',
    group: 'Native',
    source: 'Engine',
    description: 'Different native definition.',
  },
]
afterEach(() => localStorage.clear())
describe('equivalent saved metric concepts', () => {
  it('collapses only the known original-summary copy while retaining the native definition', () => {
    const row = {
      summary: { initial_capital: 100 },
      analysis: { metrics: { account_initial_capital: 100, native_starting_capital: 200 } },
    } as unknown as PortfolioTrial
    const available = availableTrialMetrics([row], catalog)
    expect(available.map((metric) => metric.key)).toEqual([
      'initial_capital',
      'native_starting_capital',
    ])
    expect(trialMetricValue(available[0], row)).toBe(100)
    expect(trialMetricValue(available[1], row)).toBe(200)
  })
  it('keeps null original values and supports old analysis-only rows', () => {
    const onlyCopy = {
      summary: {},
      analysis: { metrics: { account_initial_capital: 100 } },
    } as unknown as PortfolioTrial
    const metric = availableTrialMetrics([onlyCopy], catalog)[0]
    expect(metric.key).toBe('initial_capital')
    expect(trialMetricValue(metric, onlyCopy)).toBe(100)
    expect(trialMetricValue(metric, { ...onlyCopy, summary: { initial_capital: null } })).toBeNull()
  })
  it('migrates remembered alias choices without duplicates or changing stored preferences', () => {
    const raw = JSON.stringify([
      'account_initial_capital',
      'initial_capital',
      'native_starting_capital',
    ])
    localStorage.setItem('research-trial-columns:v1:owner', raw)
    expect(readTrialColumns('owner', catalog)).toEqual([
      'initial_capital',
      'native_starting_capital',
    ])
    expect(localStorage.getItem('research-trial-columns:v1:owner')).toBe(raw)
  })
  it('does not infer equivalence from an account prefix on an unknown native metric', () => {
    const row = {
      summary: {},
      analysis: { metrics: { account_native_starting_capital: 200 } },
    } as unknown as PortfolioTrial
    expect(availableTrialMetrics([row], catalog)).toEqual([])
  })
})
