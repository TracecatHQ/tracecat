"use client"

import type { KeyboardEvent, ReactNode, SyntheticEvent } from "react"
import { cn } from "@/lib/utils"

/** Props for {@link ReadOnlyFieldset}. */
export interface ReadOnlyFieldsetProps {
  readOnly: boolean
  className?: string
  children: ReactNode
}

/** Keys that move focus or the caret without editing. */
const NAVIGATION_KEYS: ReadonlySet<string> = new Set([
  "Tab",
  "Escape",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
  "PageUp",
  "PageDown",
  "Shift",
  "Control",
  "Alt",
  "Meta",
])

function isContentEditableTarget(event: SyntheticEvent): boolean {
  return event.target instanceof HTMLElement && event.target.isContentEditable
}

/** Cancel an edit event aimed at a rich text or code editor. */
function blockEdit(event: SyntheticEvent) {
  if (isContentEditableTarget(event)) {
    event.preventDefault()
    event.stopPropagation()
  }
}

/** Cancel key presses that would edit a rich text or code editor. */
function blockEditingKey(event: KeyboardEvent) {
  if (!isContentEditableTarget(event) || NAVIGATION_KEYS.has(event.key)) {
    return
  }
  const modifier = event.metaKey || event.ctrlKey
  const key = event.key.toLowerCase()
  // Copy and select-all do not edit.
  if (modifier && (key === "c" || key === "a")) {
    return
  }
  event.preventDefault()
  event.stopPropagation()
}

/**
 * Wrap a group of form controls so they can be locked as a unit.
 *
 * When `readOnly` is true, the native `disabled` fieldset disables inputs,
 * selects, textareas and buttons inside it, and pointer events are turned off
 * on its children so custom controls such as code editors and comboboxes
 * cannot be clicked either. A disabled fieldset does not reach contenteditable
 * editors (CodeMirror, TipTap), so edit keys, typing, paste, cut and drop
 * aimed at them are cancelled in the capture phase, before the editor sees
 * them. The fieldset itself keeps pointer events, so a scroll container
 * around or on it still scrolls.
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
      onKeyDownCapture={readOnly ? blockEditingKey : undefined}
      onBeforeInputCapture={readOnly ? blockEdit : undefined}
      onPasteCapture={readOnly ? blockEdit : undefined}
      onCutCapture={readOnly ? blockEdit : undefined}
      onDropCapture={readOnly ? blockEdit : undefined}
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
