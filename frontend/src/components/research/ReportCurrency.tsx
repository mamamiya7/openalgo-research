import { createContext, useContext } from 'react'

// Native cash-equity reports keep their existing default. Frozen external or old
// evidence supplies null explicitly instead of manufacturing a currency label.
export const ReportCurrency = createContext<string | null>('INR')
export const useReportCurrency = () => useContext(ReportCurrency)
export function reportMoney(value: unknown, currency: string | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  if (!currency) return '—'
  try {
    return value.toLocaleString('en-IN', { style: 'currency', currency })
  } catch {
    return `${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })} ${currency}`
  }
}
