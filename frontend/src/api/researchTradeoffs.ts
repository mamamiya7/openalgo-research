import { webClient } from './client'

export interface TradeoffMetrics {
  net_return_pct: number
  max_drawdown_pct: number
  closed_trades: number
  entry_dates?: number
}
export interface ResearchTradeoffsView {
  version: 'research-tradeoffs-view-v1'
  job_id: string
  result_artifact: string | null
  available: boolean
  reason: string | null
  analysis: {
    version: string
    status: string
    counts: { recorded: number; eligible: number; frontier: number; excluded: number }
    baseline: {
      config_id: string | null
      metrics: TradeoffMetrics | null
      selected: boolean
    } | null
    choices: Array<{
      config_id: string
      roles: string[]
      metrics: TradeoffMetrics
      selected_existing: boolean
      validation: string
    }>
    limitations: string[]
  } | null
}

export const researchTradeoffs = {
  async get(
    jobId: string,
    resultArtifact: string,
    signal?: AbortSignal
  ): Promise<ResearchTradeoffsView> {
    return (
      await webClient.get(
        `/scanner-research/api/portfolio/jobs/${encodeURIComponent(jobId)}/tradeoffs`,
        { params: { expected_result: resultArtifact }, signal, timeout: 15000 }
      )
    ).data
  },
}
