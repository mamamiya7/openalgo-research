import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import type { ConnectorCatalog } from '@/api/scannerResearch'
import { type Draft, freshDraft, specification } from '@/lib/researchDraft'
import { ConnectorSettings, connectorIssue } from './ConnectorSettings'
import { ResearchSetup } from './ResearchSetup'

const catalog: ConnectorCatalog = {
  engines: [
    { id: 'scanner', name: 'Scanner', available: true },
    { id: 'vectorbt', name: 'VectorBT', available: true },
  ],
  optimizers: [{ id: 'optuna', name: 'Optuna', available: true }],
}
function Form({ initial = freshDraft() }: { initial?: Draft }) {
  const [draft, setDraft] = useState(initial)
  return (
    <>
      <ConnectorSettings draft={draft} catalog={catalog} />
      <ResearchSetup draft={draft} onChange={setDraft} />
      <output data-testid="request">{JSON.stringify(specification(draft))}</output>
    </>
  )
}

describe('research connector controls', () => {
  it('starts on VectorBT without an automatic or legacy engine picker', () => {
    render(<Form />)
    expect(screen.getByText('VectorBT · Daily backtest')).toBeVisible()
    expect(screen.getByTestId('request')).toHaveTextContent('"engine":"vectorbt"')
    expect(screen.getByTestId('request')).toHaveTextContent('"optimizer":"native"')
    expect(screen.queryByLabelText('Backtesting engine')).not.toBeInTheDocument()
    expect(screen.queryByText('Calculation tools')).not.toBeInTheDocument()
  })

  it('shows editable strategy settings without fixed engine choices', () => {
    render(<Form />)
    expect(screen.getByLabelText('Maximum holding sessions')).toBeEnabled()
    expect(screen.getByLabelText('Costs per side (basis points)')).toBeEnabled()
    expect(screen.getByLabelText('Slippage per side (basis points)')).toBeEnabled()
    for (const label of [
      'Holding period',
      'Enter no earlier than (IST)',
      'Maximum exposure (%)',
      'Scanner-count trigger',
      'Entry priority',
      'Exposure and cash fill policy',
    ])
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    expect(screen.getByText(/Protective exits start one session after entry/)).toBeInTheDocument()
  })

  it('uses Optuna for optimization without advertising adaptive sampling or Bypass choices', () => {
    render(<Form />)
    fireEvent.change(screen.getByLabelText('Run type'), { target: { value: 'optimize' } })
    expect(screen.getByText('VectorBT · Optuna optimization')).toBeVisible()
    expect(screen.getByTestId('request')).toHaveTextContent('"optimizer":"optuna"')
    expect(screen.queryByLabelText('Optimizer')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Search holding period in')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Bypass')).not.toBeInTheDocument()
    expect(screen.queryByText('Explicit trailing states')).not.toBeInTheDocument()
    expect(screen.queryByText('Auto: broad then nearby settings')).not.toBeInTheDocument()
    expect(screen.queryByText('Test on later periods')).not.toBeInTheDocument()
  })

  it('uses Optuna when exploring a saved fixed VectorBT setup', () => {
    render(
      <Form initial={{ ...freshDraft(), execution: { engine: 'vectorbt', optimizer: 'native' } }} />
    )
    fireEvent.change(screen.getByLabelText('Run type'), { target: { value: 'optimize' } })
    expect(screen.getByTestId('request')).toHaveTextContent('"optimizer":"optuna"')
  })

  it('labels preserved legacy setups and leaves their financial choices editable', () => {
    const draft = freshDraft()
    draft.execution = undefined
    draft.config.entry_priority = 'shuffle'
    render(<Form initial={draft} />)
    expect(screen.getByText('Legacy scanner setup. New run uses VectorBT.')).toBeVisible()
    expect(screen.getByTestId('request')).toHaveTextContent('{}')
    expect(screen.getByLabelText('Entry priority')).toHaveValue('shuffle')
    expect(screen.getByLabelText('Holding period')).toBeEnabled()
  })

  it('reports unavailable VectorBT rather than switching to the installed scanner engine', () => {
    const draft = freshDraft()
    render(
      <ConnectorSettings draft={draft} catalog={{ ...catalog, engines: [catalog.engines[0]] }} />
    )
    expect(screen.getByRole('alert')).toHaveTextContent('VectorBT is unavailable')
    expect(specification(draft)).toMatchObject({ execution: { engine: 'vectorbt' } })
  })

  it('preserves unsupported intraday and allocation requirements while explaining the limitation', () => {
    const draft = freshDraft()
    draft.config = {
      ...draft.config,
      trade_horizon: 'intraday',
      hold_minutes: 30,
      max_exposure_pct: 50,
    }
    render(<ConnectorSettings draft={draft} catalog={catalog} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Intraday backtesting is not available yet')
    expect(draft.config.hold_minutes).toBe(30)
    expect(draft.config.max_exposure_pct).toBe(50)
  })

  it('requires Optuna only when optimizing', () => {
    const draft = freshDraft()
    const unavailable = { ...catalog, optimizers: [] }
    expect(connectorIssue(draft, unavailable)).toBeNull()
    draft.kind = 'optimize'
    expect(connectorIssue(draft, unavailable)).toContain('Optuna is unavailable')
  })

  it('keeps saved explicit trailing choices visible rather than changing their identity', () => {
    const draft = freshDraft()
    draft.kind = 'optimize'
    draft.search.trailing_choices = [{ enabled: false, pct: 2 }]
    render(<Form initial={draft} />)
    expect(screen.getByText('Explicit trailing states')).toBeInTheDocument()
    expect(screen.getByTestId('request')).toHaveTextContent(
      '"trailing_choices":[{"enabled":false,"pct":2}]'
    )
  })

  it('preserves saved engine pins when rerunning a selected setting', () => {
    const draft = freshDraft()
    draft.execution = {
      engine: 'vectorbt',
      optimizer: 'optuna',
      engine_version: '0.28.5',
      adapter_version: 'saved-adapter',
      contract_version: 'saved-contract',
      optimizer_version: '5.0.0',
      optimizer_adapter_version: 'saved-search',
    }
    expect(specification(draft)).toEqual({
      execution: {
        engine: 'vectorbt',
        optimizer: 'native',
        engine_version: '0.28.5',
        adapter_version: 'saved-adapter',
        contract_version: 'saved-contract',
      },
    })
  })
})
