import { buttonVariants } from '@/components/ui/button'

export const portfolioCsvExamples = [
  {
    id: 'daily',
    title: 'Daily signals',
    description: 'Date only. Uses daily prices with the default settings.',
    filename: 'example-daily-signals.csv',
    csv: 'Date,Symbol\r\n2026-08-24,INFY\r\n2026-08-25,SBIN\r\n',
  },
  {
    id: 'timed',
    title: 'Timed signals',
    description: 'Date and time in IST. Uses one-minute prices.',
    filename: 'example-timed-signals.csv',
    csv: 'Date,Time,Symbol\r\n2026-08-24,10:00,INFY\r\n2026-08-25,10:30,SBIN\r\n',
  },
] as const

export function PortfolioCsvHelp() {
  return (
    <details className="text-sm">
      <summary className="w-fit cursor-pointer text-muted-foreground focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-4">
        CSV format & examples
      </summary>
      <div className="mt-4 space-y-4">
        <p className="text-muted-foreground">
          One buy signal per row for NSE stocks, dated when it was observed. Prices come from
          OpenAlgo.
        </p>
        <div className="grid gap-5 sm:grid-cols-2">
          {portfolioCsvExamples.map((example) => (
            <div key={example.id} className="min-w-0 space-y-2">
              <h3 className="font-medium">{example.title}</h3>
              <p className="text-xs text-muted-foreground">{example.description}</p>
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs leading-5">
                <code>{example.csv.trimEnd()}</code>
              </pre>
              <a
                className={buttonVariants({ variant: 'link', size: 'sm', className: '-ml-3' })}
                href={`data:text/csv;charset=utf-8,${encodeURIComponent(example.csv)}`}
                download={example.filename}
              >
                Download {example.id} example
              </a>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Illustrative signals only. Replace the dates and symbols with your own. Intraday rules in
          Settings also select minute prices automatically.
        </p>
      </div>
    </details>
  )
}
