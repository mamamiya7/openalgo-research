import { ArrowRight, Plus } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import './ResearchRunProgress.css'

export interface StudyContinuationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentProposed: number
  maxTotal?: number
  initialAdditionalTrials?: number
  amountLocked?: boolean
  studyName?: string
  pending?: boolean
  error?: string | null
  onConfirm: (additionalTrials: number) => void
}

export function StudyContinuationDialog({
  open,
  onOpenChange,
  currentProposed,
  maxTotal = 1000,
  initialAdditionalTrials,
  amountLocked = false,
  studyName,
  pending = false,
  error,
  onConfirm,
}: StudyContinuationDialogProps) {
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const limit = Number.isSafeInteger(maxTotal) && maxTotal > 0 ? Math.min(maxTotal, 1000) : 1000
  const known = Number.isSafeInteger(currentProposed) && currentProposed >= 0
  const remaining = known ? Math.max(0, limit - currentProposed) : 0
  const initial = String(initialAdditionalTrials ?? Math.min(25, remaining))
  const [additional, setAdditional] = useState(initial)
  useEffect(() => {
    if (open) setAdditional(initial)
  }, [open, initial])
  const amount = Number(additional)
  const valid =
    /^\d+$/.test(additional) && Number.isSafeInteger(amount) && amount > 0 && amount <= remaining
  const format = (value: number) => value.toLocaleString('en-IN')
  const validation = !known
    ? 'Study totals are unavailable. Reopen the study to continue.'
    : remaining === 0
      ? `This study has reached its ${format(limit)}-trial limit.`
      : !valid
        ? `Choose 1–${format(remaining)} additional trials.`
        : undefined

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="research-continuation-dialog max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl motion-reduce:animate-none sm:max-w-md"
        onOpenAutoFocus={(event) => {
          if (input.current && !input.current.disabled) {
            event.preventDefault()
            input.current.focus()
            input.current.select()
          }
        }}
      >
        <DialogHeader className="text-left">
          <DialogTitle>Add trials</DialogTitle>
          <DialogDescription>
            Continue with the same prices and search ranges. Your current results stay saved.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-5"
          noValidate
          aria-busy={pending}
          onSubmit={(event) => {
            event.preventDefault()
            if (valid && !pending) onConfirm(amount)
          }}
        >
          {studyName && (
            <p className="truncate text-sm font-medium" title={studyName}>
              {studyName}
            </p>
          )}
          {known && (
            <div className="flex items-baseline gap-3 border-b pb-4 text-sm text-muted-foreground">
              <span className="tabular-nums">{format(currentProposed)} existing trials</span>
              <ArrowRight className="size-3.5 self-center" aria-hidden="true" />
              <span className="research-continuation-budget text-xl font-semibold tabular-nums">
                {format(currentProposed + (valid ? amount : 0))}
                <span className="ml-1 text-sm font-normal text-muted-foreground">total</span>
              </span>
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor={`${id}-trials`}>Additional trials</Label>
            <Input
              ref={input}
              id={`${id}-trials`}
              type="number"
              inputMode="numeric"
              min={1}
              max={remaining}
              step={1}
              value={additional}
              onChange={(event) => {
                if (!amountLocked) setAdditional(event.target.value)
              }}
              readOnly={amountLocked}
              disabled={pending || remaining === 0}
              aria-invalid={!!validation}
              aria-describedby={validation ? `${id}-validation` : undefined}
              className="h-11 tabular-nums"
            />
            {validation && (
              <p id={`${id}-validation`} className="text-sm text-destructive">
                {validation}
              </p>
            )}
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!valid || pending}>
              {!pending && <Plus className="size-4" aria-hidden="true" />}
              {pending ? 'Starting…' : 'Add trials'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
