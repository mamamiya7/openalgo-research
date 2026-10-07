import type { Chart } from 'openalgo-charts'
import {
  type DataWindowHandle,
  injectWidgetStyles,
  mountDataWindow,
  WIDGET_COMPONENT_CSS,
  type WidgetContext,
} from 'openalgo-charts/widget'

/** Native read-only readings. No feed, orders, storage or terminal dependencies. */
export function mountChartDataWindow(chart: Chart, host: HTMLElement): DataWindowHandle {
  const document = host.ownerDocument
  injectWidgetStyles(document, WIDGET_COMPONENT_CSS)
  return mountDataWindow({ chart, document, locale: 'en-IN' } as WidgetContext, host)
}
