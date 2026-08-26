/**
 * @jest-environment jsdom
 */

import { renderHook } from "@testing-library/react"
import { useDigitShortcuts } from "@/hooks/use-digit-shortcuts"

interface PressOptions {
  target?: HTMLElement
  repeat?: boolean
  metaKey?: boolean
  ctrlKey?: boolean
  altKey?: boolean
  shiftKey?: boolean
}

function press(key: string, options: PressOptions = {}): KeyboardEvent {
  const { target, ...init } = options
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  })
  ;(target ?? document.body).dispatchEvent(event)
  return event
}

function mount(container: HTMLElement): void {
  document.body.appendChild(container)
}

describe("useDigitShortcuts", () => {
  let onDigit: jest.Mock

  beforeEach(() => {
    onDigit = jest.fn()
    document.body.innerHTML = ""
  })

  it("fires onDigit with the 1-based digit for a bare press", () => {
    renderHook(() => useDigitShortcuts({ count: 6, onDigit }))

    const event = press("3")

    expect(onDigit).toHaveBeenCalledTimes(1)
    expect(onDigit).toHaveBeenCalledWith(3)
    expect(event.defaultPrevented).toBe(true)
  })

  it("ignores digits above count, zero, and non-digit keys", () => {
    renderHook(() => useDigitShortcuts({ count: 6, onDigit }))

    press("7")
    press("0")
    press("a")
    press("F1")
    press("Enter")

    expect(onDigit).not.toHaveBeenCalled()
  })

  it("ignores key repeat and every modifier, including Shift", () => {
    renderHook(() => useDigitShortcuts({ count: 6, onDigit }))

    press("4", { repeat: true })
    press("1", { metaKey: true })
    press("1", { ctrlKey: true })
    press("1", { altKey: true })
    // Blocking Shift also stops Shift+1 producing "!" from reading as 1.
    press("1", { shiftKey: true })
    press("!", { shiftKey: true })

    expect(onDigit).not.toHaveBeenCalled()
  })

  it("ignores presses inside form fields", () => {
    renderHook(() => useDigitShortcuts({ count: 6, onDigit }))

    const input = document.createElement("input")
    const textarea = document.createElement("textarea")
    const select = document.createElement("select")
    mount(input)
    mount(textarea)
    mount(select)

    press("2", { target: input })
    press("2", { target: textarea })
    press("2", { target: select })

    expect(onDigit).not.toHaveBeenCalled()
  })

  it("ignores presses on nodes nested inside a contenteditable root", () => {
    renderHook(() => useDigitShortcuts({ count: 6, onDigit }))

    // Mirrors tiptap: the keydown target is a nested node, not the
    // contenteditable element itself, so only a closest() sweep catches it.
    const editor = document.createElement("div")
    editor.setAttribute("contenteditable", "true")
    const nested = document.createElement("span")
    editor.appendChild(nested)
    mount(editor)

    press("3", { target: nested })

    expect(onDigit).not.toHaveBeenCalled()
  })

  it("ignores presses on textbox and combobox roles", () => {
    renderHook(() => useDigitShortcuts({ count: 6, onDigit }))

    const textbox = document.createElement("div")
    textbox.setAttribute("role", "textbox")
    const combobox = document.createElement("button")
    combobox.setAttribute("role", "combobox")
    mount(textbox)
    mount(combobox)

    press("1", { target: textbox })
    press("1", { target: combobox })

    expect(onDigit).not.toHaveBeenCalled()
  })

  it("ignores presses targeted inside a dialog", () => {
    renderHook(() => useDigitShortcuts({ count: 6, onDigit }))

    const dialog = document.createElement("div")
    dialog.setAttribute("role", "dialog")
    const button = document.createElement("button")
    dialog.appendChild(button)
    mount(dialog)

    press("3", { target: button })

    expect(onDigit).not.toHaveBeenCalled()
  })

  it("ignores presses while an open dialog exists anywhere in the document", () => {
    renderHook(() => useDigitShortcuts({ count: 6, onDigit }))

    // Radix portals dialogs to document.body; with focus left on the body,
    // the keydown target is outside the dialog subtree entirely.
    const dialog = document.createElement("div")
    dialog.setAttribute("role", "dialog")
    dialog.setAttribute("data-state", "open")
    mount(dialog)

    press("3")

    expect(onDigit).not.toHaveBeenCalled()
  })

  it("does not bind while disabled and unbinds on unmount", () => {
    const { rerender, unmount } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useDigitShortcuts({ count: 6, onDigit, enabled }),
      { initialProps: { enabled: false } }
    )

    press("2")
    expect(onDigit).not.toHaveBeenCalled()

    rerender({ enabled: true })
    press("2")
    expect(onDigit).toHaveBeenCalledWith(2)

    unmount()
    press("2")
    expect(onDigit).toHaveBeenCalledTimes(1)
  })
})
