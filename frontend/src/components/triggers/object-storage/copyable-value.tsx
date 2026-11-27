"use client"

import { CopyButton } from "@/components/copy-button"

/** A value shown in full, selectable, with a copy button. */
export function CopyableValue({
  value,
  toastMessage,
}: {
  value: string
  toastMessage: string
}) {
  return (
    <div className="flex min-w-0 max-w-full items-start gap-3 rounded-md bg-muted/60 px-3 py-2.5">
      <code className="min-w-0 flex-1 whitespace-pre-wrap break-all font-mono text-xs text-foreground/90 select-all">
        {value}
      </code>
      <CopyButton
        value={value}
        toastMessage={toastMessage}
        tooltipMessage="Copy"
        className="size-6 shrink-0"
        iconClassName="size-4 text-foreground/70"
      />
    </div>
  )
}
