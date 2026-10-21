import { describe, expect, it } from 'vitest'
import { researchFeaturePreparation, researchRecipeLabel } from './researchRecipeLabels'

describe('saved research recipe labels', () => {
  it.each([
    ['automatic-trade-management-v1', 'Exits and holding periods'],
    ['automatic-trade-management-v2', 'Stability-checked exits'],
    ['automatic-entry-conditions-v1', 'Exits and market entry conditions'],
    ['automatic-hypotheses-v2', 'Trend and momentum'],
    ['automatic-hypotheses-v3', 'Stock indicators'],
    ['automatic-hypotheses-v4', 'Smart research'],
    ['automatic-hypotheses-v5', 'Stock and market research'],
    ['automatic-hypotheses-v6', 'Stock, market and trailing stops'],
    ['automatic-hypotheses-v7', 'Stock, market and trade timing'],
    ['automatic-hypotheses-v8', 'Walk-forward research'],
    ['automatic-hypotheses-v9', 'Expanded automatic research'],
    ['automatic-hypotheses-v10', 'Execution-aware research'],
  ])('describes the recorded scope of %s', (version, label) => {
    expect(researchRecipeLabel(version)).toBe(label)
  })

  it.each([
    undefined,
    null,
    '',
    'automatic-hypotheses-v99',
    'toString',
    '__proto__',
  ])('does not infer scope for an unrecognized recipe %s', (version) => {
    expect(researchRecipeLabel(version)).toBe('Automatic research')
    expect(researchFeaturePreparation(version)).toBe('Preparing research inputs')
  })

  it('distinguishes market-only preparation from stock indicator research', () => {
    expect(researchFeaturePreparation('automatic-entry-conditions-v1')).toBe(
      'Preparing market conditions'
    )
    expect(researchFeaturePreparation('automatic-hypotheses-v4')).toBe('Preparing stock indicators')
    expect(researchFeaturePreparation('automatic-hypotheses-v5')).toBe(
      'Preparing stock and market conditions'
    )
    expect(researchFeaturePreparation('automatic-hypotheses-v8')).toBe(
      'Preparing stock and market conditions'
    )
    expect(researchFeaturePreparation('automatic-hypotheses-v7')).toBe(
      'Preparing stock and market conditions'
    )
  })
})
