"use client"

import { useId } from "react"
import { CopyButton } from "@/components/copy-button"
import { Label } from "@/components/ui/label"

/** A read-only value to paste into the provider, with a copy control. */
export function CopyField({
  label,
  value,
  help,
  warning,
}: {
  label: string
  value: string
  help?: string
  /** Shown under the input and read as its description. */
  warning?: string | null
}) {
  const id = useId()
  const warningId = `${id}-warning`
  const helpId = `${id}-help`
  const describedBy =
    [warning ? warningId : null, help ? helpId : null]
      .filter(Boolean)
      .join(" ") || undefined
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3 rounded-md border bg-muted/40 px-3 py-2">
        <div className="flex min-w-0 flex-1 flex-col">
          <Label
            htmlFor={id}
            className="text-xs font-normal text-muted-foreground"
          >
            {label}
          </Label>
          <input
            id={id}
            readOnly
            value={value}
            aria-describedby={describedBy}
            className="w-full truncate bg-transparent font-mono text-sm outline-none"
          />
        </div>
        <CopyButton
          value={value}
          toastMessage={`${label} copied`}
          tooltipMessage={`Copy ${label.toLowerCase()}`}
          className="size-7 shrink-0"
          iconClassName="size-3.5"
        />
      </div>
      {warning ? (
        <p id={warningId} className="text-xs text-destructive">
          {warning}
        </p>
      ) : null}
      {help ? (
        <p id={helpId} className="text-xs text-muted-foreground">
          {help}
        </p>
      ) : null}
    </div>
  )
}
