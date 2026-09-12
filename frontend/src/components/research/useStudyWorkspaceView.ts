import { useEffect, useState } from 'react'
import type { StudyPoint } from './studyPresentation'
import { defaultStudyTrialView, type StudyTrialView } from './studyTrialView'

export interface StudyWorkspaceView {
  surface?: 'study' | 'report'
  section: 'overview' | 'parameters' | 'trials' | 'activity'
  trials: StudyTrialView
  candidate: StudyPoint | null
  parameters: string[]
  advanced: boolean
}
export const defaultStudyWorkspaceView: StudyWorkspaceView = {
  section: 'overview',
  trials: defaultStudyTrialView,
  candidate: null,
  parameters: [],
  advanced: false,
}
const storageKey = 'research-study-navigation-v1'
type Entry = { key: string; view: StudyWorkspaceView }
const identityText = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0 && value.length <= 128
function entries(): Entry[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? '[]')
    return Array.isArray(value) ? value.slice(-20) : []
  } catch {
    return []
  }
}
export function readStudyView(key: string): StudyWorkspaceView {
  const value = entries().find((entry) => entry?.key === key)?.view
  if (!value || !['overview', 'parameters', 'trials', 'activity'].includes(value.section))
    return defaultStudyWorkspaceView
  const trials = value.trials
  if (
    !trials ||
    !['distinct', 'all'].includes(trials.scope) ||
    !['asc', 'desc'].includes(trials.sortDirection) ||
    !identityText(trials.sortKey) ||
    !Number.isInteger(trials.page) ||
    trials.page < 0 ||
    trials.page > 1000
  )
    return defaultStudyWorkspaceView
  return {
    ...defaultStudyWorkspaceView,
    ...value,
    trials: {
      ...trials,
      selectedConfigId: identityText(trials.selectedConfigId) ? trials.selectedConfigId : null,
    },
    surface: value.surface === 'report' ? 'report' : 'study',
    candidate:
      value.candidate &&
      identityText(value.candidate.configId) &&
      Number.isSafeInteger(value.candidate.proposalNumber) &&
      value.candidate.proposalNumber >= 0
        ? { configId: value.candidate.configId, proposalNumber: value.candidate.proposalNumber }
        : null,
    parameters: Array.isArray(value.parameters)
      ? value.parameters.filter(identityText).slice(0, 2)
      : [],
    advanced: value.advanced === true,
  }
}
export function writeStudyView(key: string, view: StudyWorkspaceView) {
  try {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify([...entries().filter((entry) => entry?.key !== key), { key, view }].slice(-20))
    )
  } catch {
    /* Browsing works when session storage is disabled. */
  }
}
/** Tab-local navigation only; scientific choices and cross-device preferences are saved separately. */
export function useStudyWorkspaceView(key: string) {
  const [state, setState] = useState(() => ({ key, view: readStudyView(key) }))
  const view = state.key === key ? state.view : readStudyView(key)
  useEffect(() => {
    if (state.key !== key) setState({ key, view: readStudyView(key) })
  }, [key, state.key])
  const update = (next: StudyWorkspaceView) => {
    writeStudyView(key, next)
    setState({ key, view: next })
  }
  return [view, update] as const
}
