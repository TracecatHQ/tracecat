"use client"

import { type RefObject, useEffect } from "react"

interface TypeToFocusTarget {
  ref: RefObject<HTMLTextAreaElement | HTMLInputElement | null>
  /** Bumped whenever the target gains focus; higher wins. */
  priority: number
}

const targets: TypeToFocusTarget[] = []
let priorityCounter = 0
let detachListeners: (() => void) | null = null

/**
 * Returns `true` when a keystroke on `target` is already typing somewhere or
 * belongs to a widget that consumes keys (comboboxes, rich-text editors).
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
      "[contenteditable='true'], [role='textbox'], [role='combobox'], [role='listbox'], [role='menu'], [role='menubar'], [role='grid']"
    )
  )
}

/**
 * Returns `true` while a layer the field does not live in owns the keyboard:
 * an open popover or menu, or a dialog stacked over the field.
 */
function isBlockedByLayer(
  target: EventTarget | null,
  field: HTMLElement
): boolean {
  if (
    target instanceof HTMLElement &&
    target.closest("[data-radix-popper-content-wrapper]")
  ) {
    return true
  }
  const openDialogs = document.querySelectorAll(
    "[role='dialog'][data-state='open'], [role='alertdialog'][data-state='open']"
  )
  for (const dialog of openDialogs) {
    if (!dialog.contains(field)) {
      return true
    }
  }
  return false
}

function isVisible(element: HTMLElement): boolean {
  if (!element.isConnected) {
    return false
  }
  if (typeof element.checkVisibility === "function") {
    return element.checkVisibility({ visibilityProperty: true })
  }
  return element.getClientRects().length > 0
}

function isEligible(
  field: HTMLTextAreaElement | HTMLInputElement | null
): field is HTMLTextAreaElement | HTMLInputElement {
  return Boolean(
    field &&
      !field.disabled &&
      !field.readOnly &&
      !field.closest("[inert], [aria-hidden='true']") &&
      isVisible(field)
  )
}

function insertText(
  field: HTMLTextAreaElement | HTMLInputElement,
  text: string
): void {
  if (document.activeElement !== field) {
    const end = field.value.length
    field.focus()
    field.setSelectionRange(end, end)
  }
  // `insertText` keeps native undo and fires the `input` event React reads.
  if (document.execCommand?.("insertText", false, text)) {
    return
  }
  const start = field.selectionStart ?? field.value.length
  const end = field.selectionEnd ?? start
  field.setRangeText(text, start, end, "end")
  field.dispatchEvent(new Event("input", { bubbles: true }))
}

function pickTarget(
  eventTarget: EventTarget | null
): HTMLTextAreaElement | HTMLInputElement | null {
  const ordered = [...targets].sort((a, b) => b.priority - a.priority)
  for (const { ref } of ordered) {
    const field = ref.current
    if (isEligible(field) && !isBlockedByLayer(eventTarget, field)) {
      return field
    }
  }
  return null
}

function handleKeyDown(event: KeyboardEvent): void {
  if (
    event.defaultPrevented ||
    event.isComposing ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey
  ) {
    return
  }
  // Printable characters only. Whitespace keeps its native meaning (space
  // activates buttons and scrolls the page), and no message starts with it.
  if (event.key.length !== 1 || /\s/.test(event.key)) {
    return
  }
  const eventTarget = event.target
  if (isEditableTarget(eventTarget)) {
    return
  }
  const key = event.key
  // Page shortcuts such as the case panel digits also listen on `window`.
  // Wait for the dispatch to finish so any handler that claims the key with
  // `preventDefault` wins over typing into the chat.
  window.setTimeout(() => {
    if (event.defaultPrevented) {
      return
    }
    // A burst of keys can all be queued before the first one moves focus;
    // later keys follow it into the same field instead of being dropped.
    const active = document.activeElement
    const activeField = targets.find(({ ref }) => ref.current === active)?.ref
      .current
    if (activeField) {
      if (isEligible(activeField)) {
        insertText(activeField, key)
      }
      return
    }
    if (active !== eventTarget && isEditableTarget(active)) {
      return
    }
    const field = pickTarget(eventTarget)
    if (field) {
      insertText(field, key)
    }
  }, 0)
}

function handleFocusIn(event: FocusEvent): void {
  const target = targets.find(({ ref }) => ref.current === event.target)
  if (target) {
    priorityCounter += 1
    target.priority = priorityCounter
  }
}

function attachListeners(): void {
  if (detachListeners) {
    return
  }
  window.addEventListener("keydown", handleKeyDown)
  document.addEventListener("focusin", handleFocusIn)
  detachListeners = () => {
    window.removeEventListener("keydown", handleKeyDown)
    document.removeEventListener("focusin", handleFocusIn)
  }
}

/** Options for {@link useTypeToFocus}. */
export interface UseTypeToFocusOptions {
  /** Registers the field only while `true`. Defaults to `true`. */
  enabled?: boolean
}

/**
 * Sends printable keystrokes typed while nothing editable has focus into
 * `ref`'s field, so a visible chat composer catches typing without a click.
 *
 * When several fields are registered, the most recently focused visible one
 * wins, then the most recently mounted. Keys are left alone when they carry
 * Ctrl/Meta/Alt, are whitespace, land in another input, menu, or combobox,
 * happen behind an open dialog, or another handler calls `preventDefault`.
 */
export function useTypeToFocus(
  ref: RefObject<HTMLTextAreaElement | HTMLInputElement | null>,
  { enabled = true }: UseTypeToFocusOptions = {}
): void {
  useEffect(() => {
    if (!enabled) {
      return
    }
    priorityCounter += 1
    const target: TypeToFocusTarget = { ref, priority: priorityCounter }
    targets.push(target)
    attachListeners()
    return () => {
      const index = targets.indexOf(target)
      if (index !== -1) {
        targets.splice(index, 1)
      }
      if (targets.length === 0) {
        detachListeners?.()
        detachListeners = null
      }
    }
  }, [ref, enabled])
}
