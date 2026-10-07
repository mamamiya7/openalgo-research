import type { ChosenEntryConditions as Conditions } from '@/api/researchChosenSetups'

const names = {
  up: 'Rising market',
  down: 'Falling market',
  range: 'Sideways market',
  normal: 'Normal volatility',
  high: 'High volatility',
}

export function ChosenEntryConditions({
  conditions,
  strategies,
}: {
  conditions?: Conditions
  strategies: Array<{ id: string; name: string }>
}) {
  if (!conditions) return null
  return (
    <section
      aria-label="Saved entry conditions"
      className="space-y-1 text-xs text-muted-foreground"
    >
      {conditions.version !== 'research-entry-conditions-v1' ? (
        <p>{conditions.hypothesis.label} entries only.</p>
      ) : conditions.rules.map((rule) => (
        <p key={rule.strategy_id}>
          {strategies.find((strategy) => strategy.id === rule.strategy_id)?.name ??
            rule.strategy_id}
          : {names[rule.regime]} entries only.
        </p>
      ))}
      <p>
        Conditions use completed information before entry. Missing condition history skips the
        signal.
      </p>
      <p>Reusing these rules does not make previously viewed dates untouched validation.</p>
    </section>
  )
}
