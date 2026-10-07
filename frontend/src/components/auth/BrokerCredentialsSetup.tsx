import { Copy, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { webClient } from '@/api/client'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

interface Credentials {
  current_broker: string
  valid_brokers: string[]
  redirect_url: string
  broker_api_key_raw_length: number
}

const keyHints: Record<string, string> = {
  fivepaisa: 'API key format: User_Key:::User_ID:::client_id',
  flattrade: 'API key format: client_id:::api_key',
  dhan: 'API key format: client_id:::api_key',
  indmoney: 'Use the Client ID as API key. Leave the secret empty for MPIN + TOTP login.',
}

/** The native Profile credential API and field rules, limited to broker setup. */
export function BrokerCredentialsSetup({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const [credentials, setCredentials] = useState<Credentials | null>(null)
  const [broker, setBroker] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [secret, setSecret] = useState('')
  const [marketKey, setMarketKey] = useState('')
  const [marketSecret, setMarketSecret] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setCredentials(null)
    setError('')
    setSaved(false)
    setCopied(false)
    setApiKey('')
    setSecret('')
    setMarketKey('')
    setMarketSecret('')
    webClient
      .get<{ status: string; data: Credentials }>('/api/broker/credentials', {
        signal: controller.signal,
        timeout: 30000,
      })
      .then(({ data }) => {
        if (controller.signal.aborted) return
        if (data.status !== 'success') throw new Error('Could not load broker settings.')
        setCredentials(data.data)
        setBroker(data.data.current_broker)
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError('Could not load broker settings. Sign in again and retry.')
      })
    return () => controller.abort()
  }, [open])

  const origin =
    credentials?.redirect_url.match(/^(https?:\/\/[^/]+)/)?.[1] || window.location.origin
  const callback = `${origin}/${broker}/callback`
  const needsKey = broker !== credentials?.current_broker || !credentials?.broker_api_key_raw_length
  const hasChanges =
    apiKey || secret || marketKey || marketSecret || broker !== credentials?.current_broker

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const payload: Record<string, string> = { redirect_url: callback }
      if (apiKey.trim()) payload.broker_api_key = apiKey.trim()
      if (secret.trim()) payload.broker_api_secret = secret.trim()
      if (marketKey.trim()) payload.broker_api_key_market = marketKey.trim()
      if (marketSecret.trim()) payload.broker_api_secret_market = marketSecret.trim()
      const { data } = await webClient.post<{ status: string; message?: string }>(
        '/api/broker/credentials',
        payload,
        { timeout: 30000 }
      )
      if (data.status !== 'success') throw new Error(data.message || 'Could not save credentials.')
      setApiKey('')
      setSecret('')
      setMarketKey('')
      setMarketSecret('')
      setSaved(true)
      onSaved()
    } catch (cause) {
      const failure = cause as { response?: { data?: { message?: string } }; message?: string }
      setError(failure.response?.data?.message || failure.message || 'Could not save credentials.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!saving) onOpenChange(value)
      }}
    >
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Broker credentials</DialogTitle>
          <DialogDescription>
            Add the API credentials from your broker’s developer portal.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {saved ? (
          <div className="space-y-4">
            <output className="block">
              Saved. Restart OpenAlgo, then return here to connect your broker.
            </output>
            <p className="text-sm text-muted-foreground">
              Stop the launcher with Ctrl+C, then open Start.cmd (Windows) or run bash
              start-research.sh (Linux).
            </p>
            <Button onClick={() => onOpenChange(false)}>Done</Button>
          </div>
        ) : credentials ? (
          <form onSubmit={save} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="setup-broker">Broker</Label>
              <Select
                value={broker}
                onValueChange={setBroker}
                disabled={saving || credentials.broker_api_key_raw_length > 0}
              >
                <SelectTrigger id="setup-broker">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {credentials.valid_brokers.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {credentials.broker_api_key_raw_length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Change broker in Profile after connecting.
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="setup-callback">Callback URL</Label>
              <div className="flex gap-2">
                <Input
                  id="setup-callback"
                  value={callback}
                  readOnly
                  onFocus={(event) => event.target.select()}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label="Copy callback URL"
                  onClick={() => {
                    navigator.clipboard
                      .writeText(callback)
                      .then(() => setCopied(true))
                      .catch(() => setError('Select the callback URL and copy it.'))
                  }}
                >
                  <Copy className="size-4" />
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                {copied ? 'Copied. ' : ''}Register this URL in your broker app.
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="setup-api-key">Broker API key</Label>
              <Input
                id="setup-api-key"
                type="password"
                autoComplete="new-password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                required={needsKey}
                disabled={saving}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="setup-secret">Broker API secret</Label>
              <Input
                id="setup-secret"
                type="password"
                autoComplete="new-password"
                value={secret}
                onChange={(event) => setSecret(event.target.value)}
                disabled={saving}
              />
              <p className="text-xs text-muted-foreground">
                Only if your broker provides one. Empty fields keep saved values.
              </p>
            </div>
            {keyHints[broker] && (
              <p className="text-sm text-muted-foreground">{keyHints[broker]}</p>
            )}
            <details>
              <summary className="cursor-pointer text-sm">
                Separate market-data keys (XTS brokers)
              </summary>
              <div className="space-y-3 pt-3">
                <Label htmlFor="setup-market-key">Market API key</Label>
                <Input
                  id="setup-market-key"
                  type="password"
                  autoComplete="new-password"
                  value={marketKey}
                  onChange={(event) => setMarketKey(event.target.value)}
                  disabled={saving}
                />
                <Label htmlFor="setup-market-secret">Market API secret</Label>
                <Input
                  id="setup-market-secret"
                  type="password"
                  autoComplete="new-password"
                  value={marketSecret}
                  onChange={(event) => setMarketSecret(event.target.value)}
                  disabled={saving}
                />
              </div>
            </details>
            <a
              className="block text-sm underline"
              href="https://docs.openalgo.in"
              target="_blank"
              rel="noopener noreferrer"
            >
              OpenAlgo broker setup guides
            </a>
            <Button
              type="submit"
              className="w-full"
              disabled={saving || !hasChanges || (needsKey && !apiKey.trim())}
            >
              {saving && <Loader2 className="mr-2 size-4 animate-spin" />}Save broker credentials
            </Button>
          </form>
        ) : !error ? (
          <output className="flex items-center gap-2">
            <Loader2 className="size-4 animate-spin" />
            Loading broker settings…
          </output>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
