import { ArrowUpRight } from 'lucide-react'
import { type ChartinkSourceMetadata, chartinkSourceUrl } from '@/api/researchChartink'

export function ChartinkSource({ source }: { source?: ChartinkSourceMetadata }) {
  if (!source) return null
  const url = chartinkSourceUrl(source.url)
  return (
    <p className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-0.5 hover:text-primary underline-offset-4 hover:underline"
        >
          Chartink history
          <ArrowUpRight className="size-3" aria-hidden="true" />
        </a>
      ) : (
        <span>Chartink history</span>
      )}
      {source.selected_period && <span>· {source.selected_period}</span>}
      {source.repaints === true && <span>· Repainting scanner</span>}
    </p>
  )
}
