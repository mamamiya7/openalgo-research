import { lazy } from 'react'
import { useSearchParams } from 'react-router'

const ResearchLibrary = lazy(() => import('@/pages/ResearchLibrary'))
const ScannerResearch = lazy(() => import('@/pages/ScannerResearch'))

export default function ResearchEntry() {
  const [params] = useSearchParams()
  return params.get('legacy') === '1' ? <ScannerResearch /> : <ResearchLibrary />
}
