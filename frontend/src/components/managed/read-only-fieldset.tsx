"use client"

import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

/** Props for {@link ReadOnlyFieldset}. */
export interface ReadOnlyFieldsetProps {
  readOnly: boolean
  className?: string
  children: ReactNode
}

/**
 * Wrap a group of form controls so they can be locked as a unit.
 *
 * When `readOnly` is true, the native `disabled` fieldset disables inputs,
 * selects, textareas and buttons inside it, and pointer events are turned off
 * on its children so custom controls such as code editors and comboboxes
 * cannot be clicked either. The fieldset itself keeps pointer events, so a
 * scroll container around or on it still scrolls.
 */
export function ReadOnlyFieldset({
  readOnly,
  className,
  children,
}: ReadOnlyFieldsetProps) {
  return (
    <fieldset
      disabled={readOnly}
      aria-disabled={readOnly || undefined}
      className={cn(
        "m-0 min-w-0 border-0 p-0",
        readOnly && "[&>*]:pointer-events-none",
        className
      )}
    >
      {children}
    </fieldset>
  )
}
