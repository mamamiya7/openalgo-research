import { ArrowUpRight, Check, Copy, Download, Puzzle } from 'lucide-react'
import { useState } from 'react'
import { Button, buttonVariants } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const extensionDownload =
  'https://github.com/mamamiya7/openalgo-research/releases/download/chartink-v0.1.2/openalgo-chartink-0.1.2.zip'
const extensionGuide =
  'https://github.com/mamamiya7/openalgo-research/blob/main/extensions/chartink/README.md'

export function ChartinkConnect() {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'manual'>('idle')
  const origin = window.location.origin
  return (
    <Dialog onOpenChange={() => setCopyState('idle')}>
      <DialogTrigger asChild>
        <Button type="button" variant="outline">
          <Puzzle className="size-4" aria-hidden="true" />
          Import from Chartink
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Chartink to OpenAlgo</DialogTitle>
          <DialogDescription>
            Send historical scanner signals straight to a saved setup with our Chrome extension.
          </DialogDescription>
        </DialogHeader>
        <ol className="space-y-6 py-2 text-sm">
          <li className="flex gap-3">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
              1
            </span>
            <div className="min-w-0 space-y-2">
              <h3 className="font-medium">Install once in Chrome</h3>
              <a href={extensionDownload} className={buttonVariants({ size: 'sm' })}>
                <Download className="size-4" aria-hidden="true" />
                Download extension
              </a>
              <p className="text-muted-foreground">
                Extract the ZIP. Open <code>chrome://extensions</code>, turn on Developer mode, then
                choose <strong>Load unpacked</strong> and select the folder containing{' '}
                <code>manifest.json</code>. Pin it from Chrome’s Extensions menu.
              </p>
              <p className="text-xs text-muted-foreground">
                Installed manually; not yet in the Chrome Web Store.
              </p>
            </div>
          </li>
          <li className="flex gap-3">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
              2
            </span>
            <div className="min-w-0 flex-1 space-y-2">
              <h3 className="font-medium">Connect to this OpenAlgo</h3>
              <Label htmlFor="chartink-openalgo-address" className="sr-only">
                Your OpenAlgo address
              </Label>
              <div className="flex gap-2">
                <Input
                  id="chartink-openalgo-address"
                  value={origin}
                  readOnly
                  onFocus={(event) => event.target.select()}
                />
                <Button
                  type="button"
                  variant="outline"
                  aria-label={copyState === 'copied' ? 'Address copied' : 'Copy OpenAlgo address'}
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(origin)
                      setCopyState('copied')
                    } catch {
                      setCopyState('manual')
                    }
                  }}
                >
                  {copyState === 'copied' ? (
                    <Check className="size-4" aria-hidden="true" />
                  ) : (
                    <Copy className="size-4" aria-hidden="true" />
                  )}
                </Button>
              </div>
              <p className="text-muted-foreground">
                Paste this address in the extension and choose <strong>Connect</strong>. Keep
                OpenAlgo running and sign in using the same Chrome profile. No password or broker
                key goes into the extension.
              </p>
              <output className="text-xs text-muted-foreground">
                {copyState === 'copied'
                  ? 'Address copied.'
                  : copyState === 'manual'
                    ? 'Select the address above and copy it manually.'
                    : ''}
              </output>
            </div>
          </li>
          <li className="flex gap-3">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
              3
            </span>
            <div className="min-w-0 space-y-2">
              <h3 className="font-medium">Send your scanner’s history</h3>
              <p className="text-muted-foreground">
                On Chartink, open a scanner’s historical backtest, choose the period and check that{' '}
                <strong>Download → CSV</strong> is available. Click{' '}
                <strong>Research in OpenAlgo</strong> on the page or in the extension.
              </p>
              <p className="text-muted-foreground">
                Each import opens a saved experiment named after the scanner. Review its signals and
                trading rules, then run a backtest or optimization. Prices come through your
                OpenAlgo broker connection; results save in this library.
              </p>
              <a
                href="https://chartink.com/screeners"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 font-medium underline underline-offset-4"
              >
                Open Chartink <ArrowUpRight className="size-4" aria-hidden="true" />
              </a>
            </div>
          </li>
        </ol>
        <a
          href={extensionGuide}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-muted-foreground underline underline-offset-4"
        >
          Full guide, updates & troubleshooting
        </a>
      </DialogContent>
    </Dialog>
  )
}
