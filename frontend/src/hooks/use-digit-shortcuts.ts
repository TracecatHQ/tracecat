"use client"

import { useEffect } from "react"

/** Options for {@link useDigitShortcuts}. */
export interface UseDigitShortcutsOptions {
  /** Number of digits to bind, starting at `1`. `count: 6` binds `1`–`6`. */
  count: number
  /** Called with the 1-based digit that was pressed. */
  onDigit: (digit: number) => void
  /** Binds the window listener only while `true`. Defaults to `true`. */
  enabled?: boolean
}

/**
 * Returns `true` when a keystroke on `target` is typing, not a command.
 *
 * Extends the canonical check (see `nav/controls-header.tsx`) with a
 * `closest()` sweep: tiptap keydowns target nested nodes inside the
 * contenteditable root, and combobox triggers consume digits for typeahead.
 */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }
  const tagName = target.tagName
  if (
    target.isContentEditable ||
    tagName === "INPUT" ||
    tagName === "TEXTAREA" ||
    tagName === "SELECT"
  ) {
    return true
  }
  return Boolean(
    target.closest(
      "[contenteditable='true'], [role='textbox'], [role='combobox']"
    )
  )
}

/**
 * Returns `true` while a blocking layer should swallow the shortcut. Radix
 * portals dialogs to `document.body`, so their keydowns still reach a
 * `window` listener with a non-editable target — without this check, pressing
 * a digit inside a dialog would switch the panel behind it.
 */
function isBlockedByLayer(target: EventTarget | null): boolean {
  if (
    target instanceof HTMLElement &&
    target.closest(
      "[role='dialog'], [role='alertdialog'], [data-radix-popper-content-wrapper]"
    )
  ) {
    return true
  }
  return Boolean(
    document.querySelector(
      "[role='dialog'][data-state='open'], [role='alertdialog'][data-state='open']"
    )
  )
}

/**
 * Binds bare digit keys `1`–`count` (max 9) on `window`.
 *
 * Hand-rolled rather than `react-hotkeys-hook`: the house pattern is
 * `useEffect` + `window.addEventListener("keydown")`, and the library's form
 * guards miss `contenteditable`/`role="textbox"` targets anyway. Guards on
 * `event.repeat`, any modifier (blocking Shift also stops `Shift+1` → `!`
 * reading as 1), editable targets, and open Radix dialogs. Matches on
 * `event.key`, never `event.code` — `code` reports the physical key, so
 * `Digit1` on AZERTY fires for `&`.
 */
export function useDigitShortcuts({
  count,
  onDigit,
  enabled = true,
}: UseDigitShortcutsOptions): void {
  useEffect(() => {
    if (!enabled || count < 1) {
      return
    }
    const maxDigit = Math.min(count, 9)

    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.repeat ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.shiftKey
      ) {
        return
      }
      if (event.key.length !== 1) {
        return
      }
      const digit = event.key.charCodeAt(0) - "0".charCodeAt(0)
      if (digit < 1 || digit > maxDigit) {
        return
      }
      if (isEditableTarget(event.target) || isBlockedByLayer(event.target)) {
        return
      }
      event.preventDefault()
      onDigit(digit)
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [count, onDigit, enabled])
}
