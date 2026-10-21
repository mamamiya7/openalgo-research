/** Presentation only: never infer a saved recipe's scope from its name prefix. */
const recipeLabels: Record<string, string> = {
  'automatic-trade-management-v1': 'Exits and holding periods',
  'automatic-trade-management-v2': 'Stability-checked exits',
  'automatic-entry-conditions-v1': 'Exits and market entry conditions',
  'automatic-hypotheses-v2': 'Trend and momentum',
  'automatic-hypotheses-v3': 'Stock indicators',
  'automatic-hypotheses-v4': 'Smart research',
  'automatic-hypotheses-v5': 'Stock and market research',
  'automatic-hypotheses-v6': 'Stock, market and trailing stops',
  'automatic-hypotheses-v7': 'Stock, market and trade timing',
  'automatic-hypotheses-v8': 'Walk-forward research',
  'automatic-hypotheses-v9': 'Expanded automatic research',
  'automatic-hypotheses-v10': 'Execution-aware research',
  'automatic-hypotheses-v11': 'Portfolio entry research',
}

export function researchRecipeLabel(version?: string | null): string {
  return (
    (version && Object.hasOwn(recipeLabels, version) && recipeLabels[version]) ||
    'Automatic research'
  )
}

export function researchFeaturePreparation(version?: string | null): string {
  if (version === 'automatic-entry-conditions-v1') return 'Preparing market conditions'
  if (
    [
      'automatic-hypotheses-v5',
      'automatic-hypotheses-v6',
      'automatic-hypotheses-v7',
      'automatic-hypotheses-v8',
      'automatic-hypotheses-v9',
      'automatic-hypotheses-v10',
      'automatic-hypotheses-v11',
    ].includes(version ?? '')
  )
    return 'Preparing stock and market conditions'
  if (
    ['automatic-hypotheses-v2', 'automatic-hypotheses-v3', 'automatic-hypotheses-v4'].includes(
      version ?? ''
    )
  )
    return 'Preparing stock indicators'
  return 'Preparing research inputs'
}
