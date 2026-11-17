import { act, fireEvent, render, screen } from "@testing-library/react"
import { useRef, useState } from "react"
import { holdDoubleTapKey, useTypeToFocus } from "@/hooks/use-type-to-focus"

function Composer({
  label,
  enabled = true,
  disabled = false,
}: {
  label: string
  enabled?: boolean
  disabled?: boolean
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [value, setValue] = useState("")
  useTypeToFocus(ref, { enabled })
  return (
    <textarea
      aria-label={label}
      ref={ref}
      value={value}
      disabled={disabled}
      onChange={(event) => setValue(event.target.value)}
    />
  )
}

function typeOnBody(key: string, init: KeyboardEventInit = {}) {
  act(() => {
    fireEvent.keyDown(document.body, { key, ...init })
    jest.advanceTimersByTime(0)
  })
}

describe("useTypeToFocus", () => {
  beforeAll(() => {
    // jsdom does no layout, so every element reads as hidden by default.
    HTMLElement.prototype.checkVisibility = () => true
  })

  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it("routes printable keys typed on the page into the composer", () => {
    render(<Composer label="chat" />)
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("chat")

    typeOnBody("h")
    typeOnBody("i")

    expect(textarea).toHaveFocus()
    expect(textarea.value).toBe("hi")
  })

  it("keeps every key of a burst queued before focus moves", () => {
    render(<Composer label="chat" />)
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("chat")

    act(() => {
      fireEvent.keyDown(document.body, { key: "o" })
      fireEvent.keyDown(document.body, { key: "k" })
      jest.runOnlyPendingTimers()
    })

    expect(textarea.value).toBe("ok")
  })

  it("ignores modifier chords, whitespace, and non-printable keys", () => {
    render(<Composer label="chat" />)
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("chat")

    typeOnBody("k", { metaKey: true })
    typeOnBody("c", { ctrlKey: true })
    typeOnBody(" ")
    typeOnBody("Enter")
    typeOnBody("ArrowDown")

    expect(textarea).not.toHaveFocus()
    expect(textarea.value).toBe("")
  })

  it("leaves keys handled by another listener alone", () => {
    render(<Composer label="chat" />)
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("chat")
    const claim = (event: KeyboardEvent) => event.preventDefault()
    window.addEventListener("keydown", claim)

    typeOnBody("1")

    window.removeEventListener("keydown", claim)
    expect(textarea.value).toBe("")
  })

  it("does not steal keys from other inputs or open dialogs", () => {
    render(
      <>
        <Composer label="chat" />
        <input aria-label="search" />
      </>
    )
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("chat")
    const search = screen.getByLabelText<HTMLInputElement>("search")

    act(() => {
      fireEvent.keyDown(search, { key: "a" })
      jest.runOnlyPendingTimers()
    })
    expect(textarea.value).toBe("")

    const dialog = document.createElement("div")
    dialog.setAttribute("role", "dialog")
    dialog.setAttribute("data-state", "open")
    document.body.appendChild(dialog)
    typeOnBody("a")
    dialog.remove()

    expect(textarea.value).toBe("")
  })

  describe("with a double-tap shortcut key", () => {
    let release: () => void
    let toggles: number
    let pendingAt: number | null
    // Mirrors the `cc` chat toggle in `ControlsHeader`.
    function toggleOnDoubleTap(event: KeyboardEvent) {
      if (event.key !== "c") {
        pendingAt = null
        return
      }
      if (pendingAt === null) {
        pendingAt = Date.now()
        return
      }
      pendingAt = null
      event.preventDefault()
      toggles += 1
    }

    beforeEach(() => {
      toggles = 0
      pendingAt = null
      release = holdDoubleTapKey("c", 1200)
      window.addEventListener("keydown", toggleOnDoubleTap)
    })

    afterEach(() => {
      window.removeEventListener("keydown", toggleOnDoubleTap)
      release()
    })

    it("lets the shortcut fire without typing into the composer", () => {
      render(<Composer label="chat" />)
      const textarea = screen.getByLabelText<HTMLTextAreaElement>("chat")

      typeOnBody("c")
      typeOnBody("c")
      act(() => {
        jest.advanceTimersByTime(2000)
      })

      expect(toggles).toBe(1)
      expect(textarea.value).toBe("")
      expect(textarea).not.toHaveFocus()
    })

    it("types the held key ahead of the next one", () => {
      render(<Composer label="chat" />)
      const textarea = screen.getByLabelText<HTMLTextAreaElement>("chat")

      typeOnBody("c")
      expect(textarea.value).toBe("")
      typeOnBody("a")

      expect(toggles).toBe(0)
      expect(textarea.value).toBe("ca")
    })

    it("types a lone held key once the window passes", () => {
      render(<Composer label="chat" />)
      const textarea = screen.getByLabelText<HTMLTextAreaElement>("chat")

      typeOnBody("c")
      act(() => {
        jest.advanceTimersByTime(1200)
      })

      expect(textarea).toHaveFocus()
      expect(textarea.value).toBe("c")
    })
  })

  it("skips disabled composers and prefers the last focused one", () => {
    render(
      <>
        <Composer label="first" />
        <Composer label="second" />
        <Composer label="disabled" disabled />
      </>
    )
    const first = screen.getByLabelText<HTMLTextAreaElement>("first")
    const second = screen.getByLabelText<HTMLTextAreaElement>("second")

    typeOnBody("a")
    expect(second.value).toBe("a")

    act(() => {
      first.focus()
      first.blur()
    })
    typeOnBody("b")
    expect(first.value).toBe("b")
  })
})
