import { webClient } from './client'
import type { ResearchSource } from './scannerResearch'

export interface ChartinkSourceMetadata {
  url: string
  title: string
  selected_period: string
  captured_at: string
  repaints: boolean | null
  export_kind: 'chartink_history_csv'
}
export interface ChartinkImportPayload {
  version: 1
  request_id: string
  csv_text: string
  source: ChartinkSourceMetadata
}
export interface ChartinkImportResult {
  protocol_version: 1
  experiment_id: string
  source_id: string
  url: string
  receipt: ResearchSource
  reused: boolean
}
export const researchChartink = {
  async importHistory(
    payload: ChartinkImportPayload,
    signal: AbortSignal
  ): Promise<ChartinkImportResult> {
    return (
      await webClient.post('/scanner-research/api/imports/chartink', payload, {
        signal,
        timeout: 45000,
      })
    ).data
  },
}

export function chartinkSourceUrl(value: unknown): string | null {
  return typeof value === 'string' &&
    value.length <= 512 &&
    /^https:\/\/chartink\.com\/screener\/[A-Za-z0-9][A-Za-z0-9_-]{0,199}\/?$/.test(value)
    ? value
    : null
}
export const chartinkRequestId = (value: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === 'object' && !Array.isArray(value))
const text = (value: unknown, max: number): value is string =>
  typeof value === 'string' && value.trim().length > 0 && value.length <= max

export function chartinkPayload(value: unknown, requestId: string): ChartinkImportPayload | null {
  if (
    !record(value) ||
    value.version !== 1 ||
    value.request_id !== requestId ||
    !text(value.csv_text, 8 * 1024 * 1024) ||
    !record(value.source)
  )
    return null
  const source = value.source
  if (
    !chartinkSourceUrl(source.url) ||
    !text(source.title, 240) ||
    !text(source.selected_period, 80) ||
    !text(source.captured_at, 40) ||
    !/(Z|[+-]\d{2}:\d{2})$/.test(source.captured_at) ||
    !Number.isFinite(Date.parse(source.captured_at)) ||
    source.export_kind !== 'chartink_history_csv' ||
    ![true, false, null].includes(source.repaints as boolean | null)
  )
    return null
  if (new TextEncoder().encode(value.csv_text).byteLength > 8 * 1024 * 1024) return null
  return {
    version: 1,
    request_id: requestId,
    csv_text: value.csv_text,
    source: {
      url: source.url as string,
      title: source.title,
      selected_period: source.selected_period,
      captured_at: source.captured_at,
      repaints: source.repaints as boolean | null,
      export_kind: 'chartink_history_csv',
    },
  }
}
