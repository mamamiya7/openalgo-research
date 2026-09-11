import { webClient } from './client'

export type ReportExpansion =
  | 'annual'
  | 'sortino'
  | 'prices'
  | 'all_statistics'
  | 'engine_records'
  | 'drawdowns'

export interface ReportPreferences {
  headline_metrics: string[]
  statistic_metrics: string[]
  performance_view: 'return' | 'equity'
  log_equity: boolean
  rolling_window: 21 | 63 | 126
  expanded_sections: ReportExpansion[]
}

export interface ReportPreferenceReceipt {
  version: 'research-report-preferences-v1'
  revision: number
  preferences: ReportPreferences
  updated_at: number | null
}

export const defaultReportPreferences: ReportPreferences = {
  headline_metrics: [
    'account_net_return_pct',
    'account_net_pnl',
    'account_max_drawdown_pct',
    'account_sharpe_ratio',
    'account_win_rate_pct',
    'account_closed_trades',
  ],
  statistic_metrics: [
    'account_initial_capital',
    'account_final_equity',
    'account_annualized_return_pct',
    'account_sharpe_ratio',
    'account_sortino_ratio',
    'account_annualized_volatility_pct',
    'account_profit_factor',
    'account_trade_expectancy',
  ],
  performance_view: 'return',
  log_equity: false,
  rolling_window: 21,
  expanded_sections: [],
}

const url = '/scanner-research/api/library/report-preferences'
export const reportPreferences = {
  async get(signal?: AbortSignal): Promise<ReportPreferenceReceipt> {
    return (await webClient.get(url, { signal, timeout: 15000 })).data
  },
  async update(
    revision: number,
    changes: Partial<ReportPreferences>,
    signal?: AbortSignal
  ): Promise<ReportPreferenceReceipt> {
    return (await webClient.patch(url, { revision, changes }, { signal, timeout: 15000 })).data
  },
}
