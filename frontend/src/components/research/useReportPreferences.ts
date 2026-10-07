import { useCallback, useEffect, useRef, useState } from 'react'
import {
  defaultReportPreferences,
  type ReportPreferenceReceipt,
  type ReportPreferences,
  reportPreferences,
} from '@/api/reportPreferences'

export interface ReportPreferenceController {
  value: ReportPreferences
  ready: boolean
  saving: boolean
  error: string | null
  conflict: boolean
  save: (changes: Partial<ReportPreferences>) => Promise<boolean>
  reload: () => Promise<void>
}

// This hook owns one account's presentation settings, never a result artifact.
// Calls are bounded and serialized; stale tabs must review the current values.
export function useReportPreferences(owner: string | undefined): ReportPreferenceController {
  const [receipt, setReceipt] = useState<ReportPreferenceReceipt | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [pendingChanges, setPendingChanges] = useState<Partial<ReportPreferences>>({})
  const generation = useRef(0)
  const locked = useRef(false)
  const latest = useRef<ReportPreferenceReceipt | null>(null)
  const read = useRef<AbortController | null>(null)
  const write = useRef<AbortController | null>(null)

  const reload = useCallback(async () => {
    read.current?.abort()
    if (!owner || locked.current) return
    const controller = new AbortController()
    read.current = controller
    const token = generation.current
    try {
      const next = await reportPreferences.get(controller.signal)
      if (!controller.signal.aborted && token === generation.current) {
        latest.current = next
        setReceipt(next)
        setError(null)
        setConflict(false)
      }
    } catch {
      if (!controller.signal.aborted && token === generation.current)
        setError('Could not load your report preferences. Try again.')
    }
  }, [owner])

  useEffect(() => {
    generation.current += 1
    latest.current = null
    locked.current = false
    setReceipt(null)
    setSaving(false)
    setError(null)
    setConflict(false)
    setPendingChanges({})
    void reload()
    return () => {
      generation.current += 1
      read.current?.abort()
      write.current?.abort()
    }
  }, [reload])

  const save = async (changes: Partial<ReportPreferences>) => {
    const current = latest.current
    if (!owner || !current || locked.current || conflict) return false
    if (
      Object.entries(changes).every(
        ([key, value]) =>
          JSON.stringify(value) ===
          JSON.stringify(current.preferences[key as keyof ReportPreferences])
      )
    )
      return true
    locked.current = true
    setSaving(true)
    setPendingChanges(changes)
    setError(null)
    const token = generation.current
    // Cancel an older reload so it cannot replace this acknowledged write.
    read.current?.abort()
    const controller = new AbortController()
    write.current = controller
    try {
      const next = await reportPreferences.update(current.revision, changes, controller.signal)
      if (token !== generation.current) return false
      latest.current = next
      setReceipt(next)
      return true
    } catch (cause) {
      if (token !== generation.current) return false
      const failed = cause as { response?: { status?: number } }
      if (failed.response?.status === 409) {
        setConflict(true)
        setError('Preferences changed in another tab. Reload them before saving.')
      } else setError('Your report preferences were not saved. Please try again.')
      return false
    } finally {
      if (token === generation.current) {
        locked.current = false
        setSaving(false)
        setPendingChanges({})
      }
    }
  }
  return {
    value: { ...(receipt?.preferences ?? defaultReportPreferences), ...pendingChanges },
    ready: Boolean(receipt) && !conflict,
    saving,
    error,
    conflict,
    save,
    reload,
  }
}
