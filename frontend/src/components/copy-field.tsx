"use client"

import { useId } from "react"
import { CopyButton } from "@/components/copy-button"
import { Input } from "@/components/ui/input"
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
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      <div className="flex items-center gap-2">
        <Input id={id} readOnly value={value} aria-describedby={describedBy} />
        <CopyButton
          value={value}
          toastMessage={`${label} copied`}
          tooltipMessage={`Copy ${label.toLowerCase()}`}
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
