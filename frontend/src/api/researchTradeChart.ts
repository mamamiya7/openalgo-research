import { isAxiosError } from 'axios'
import { webClient } from './client'
import type { PortfolioReportContext } from './portfolioResearch'

export interface SavedTradeCandle {
  time: number
  timestamp: string
  source_timestamp?: string | number
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

export interface SavedTradeMarker {
  kind: 'entry' | 'exit'
  time: number
  timestamp: string
  price: number
  quantity: number
  basis: string
  bar_index: number
}

export interface SavedTradeChartPage {
  version: string
  identity: {
    job_id: string
    result_artifact: string
    inputs_artifact: string
    period: PortfolioReportContext['period']
    trade_index: number
  }
  symbol: string
  exchange: string
  interval: 'D' | '1m'
  timezone: string
  candles: SavedTradeCandle[]
  markers: SavedTradeMarker[]
  trade: Record<string, string | number | null>
  window: {
    offset: number
    limit: number
    total: number
    next_offset: number | null
    previous_offset: number | null
    entry_index: number | null
    exit_index: number | null
  }
  notice?: string | null
  unmapped_markers?: Array<'entry' | 'exit'>
}

export interface SavedTradeChartRequest {
  jobId: string
  resultArtifact: string
  period: PortfolioReportContext['period']
  tradeIndex: number
}

export const tradeChartError = (error: unknown): string =>
  isAxiosError(error) && typeof error.response?.data?.message === 'string'
    ? error.response.data.message
    : 'Saved prices could not be opened. Try again.'

export const researchTradeChart = {
  async get(
    request: SavedTradeChartRequest,
    offset = 0,
    signal?: AbortSignal
  ): Promise<SavedTradeChartPage> {
    return (
      await webClient.get(
        `/scanner-research/api/portfolio/jobs/${encodeURIComponent(request.jobId)}/trades/${request.tradeIndex}/chart`,
        {
          params: {
            expected_result_artifact: request.resultArtifact,
            period: request.period,
            offset,
            limit: 1500,
          },
          signal,
          timeout: 30000,
        }
      )
    ).data
  },
}
