/**
 * @jest-environment jsdom
 */

import { act, fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import {
  JsonFieldDrawer,
  LongTextFieldDrawer,
} from "@/components/cases/case-field-kind-dialogs"
import { CaseValueDrawerProvider } from "@/components/cases/case-value-drawer"

jest.mock("@/components/cases/case-description-editor", () => ({
  CaseDescriptionEditor: ({
    initialContent,
    onChange,
  }: {
    initialContent: string
    onChange: (value: string) => void
  }) => (
    <textarea
      aria-label="Rich text editor"
      defaultValue={initialContent}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

jest.mock("@uiw/react-codemirror", () => ({
  __esModule: true,
  default: ({
    value,
    onChange,
  }: {
    value: string
    onChange: (value: string) => void
  }) => (
    <textarea
      aria-label="JSON editor"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

beforeAll(() => {
  if (!HTMLElement.prototype.hasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      value: () => false,
    })
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.scrollIntoView) {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      value: () => undefined,
    })
  }
})

beforeEach(() => {
  jest.clearAllMocks()
})

/**
 * Radix registers its outside-pointer listener inside a `setTimeout(..., 0)`,
 * so the dismissal assertions are only meaningful once that timer has run.
 */
async function flushOutsideListeners() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

function clickOutside() {
  fireEvent.pointerDown(document.body)
  fireEvent.click(document.body)
}

function renderLongTextDrawer(onOpenChange: jest.Mock, onSave = jest.fn()) {
  return render(
    <LongTextFieldDrawer
      open={true}
      onOpenChange={onOpenChange}
      fieldLabel="Analyst notes"
      initialValue="<p>hello</p>"
      onSave={onSave}
    />
  )
}

function renderJsonDrawer(onOpenChange: jest.Mock, onSave = jest.fn()) {
  return render(
    <JsonFieldDrawer
      open={true}
      onOpenChange={onOpenChange}
      fieldLabel="Raw payload"
      initialValue={{ alpha: 1 }}
      onSave={onSave}
    />
  )
}

const drawerCases: Array<{
  name: string
  editorLabel: string
  renderDrawer: (onOpenChange: jest.Mock, onSave?: jest.Mock) => void
}> = [
  {
    name: "LongTextFieldDrawer",
    editorLabel: "Rich text editor",
    renderDrawer: renderLongTextDrawer,
  },
  {
    name: "JsonFieldDrawer",
    editorLabel: "JSON editor",
    renderDrawer: renderJsonDrawer,
  },
]

describe.each(drawerCases)("$name", ({ editorLabel, renderDrawer }) => {
  it("has no cancel button", () => {
    renderDrawer(jest.fn())

    expect(
      screen.queryByRole("button", { name: /cancel/i })
    ).not.toBeInTheDocument()
  })

  it("is non-modal: no overlay, and the page behind stays reachable", () => {
    render(<button type="button">Chat input</button>)
    renderDrawer(jest.fn())

    const drawer = screen.getByRole("dialog")
    expect(drawer).not.toHaveAttribute("aria-modal", "true")
    expect(drawer.className).toContain("left-0")
    expect(drawer.className).not.toContain("shadow-lg")
    expect(document.querySelector(".backdrop-blur-sm")).toBeNull()
    // A modal dialog would hide its siblings from assistive tech and block
    // their pointer events.
    expect(screen.getByRole("button", { name: "Chat input" })).toBeVisible()
    expect(document.body.style.pointerEvents).not.toBe("none")
    expect(document.querySelector("[aria-hidden='true']")).toBeNull()
  })

  it("does not close on a click outside", async () => {
    const onOpenChange = jest.fn()
    renderDrawer(onOpenChange)
    await flushOutsideListeners()

    clickOutside()

    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("does not close when focus moves outside", async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    render(<input aria-label="Chat input" />)
    renderDrawer(onOpenChange)
    await flushOutsideListeners()

    await user.click(screen.getByRole("textbox", { name: "Chat input" }))
    await user.keyboard("hello")

    expect(screen.getByRole("textbox", { name: "Chat input" })).toHaveValue(
      "hello"
    )
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("leaves Escape alone while focus is outside the drawer", async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    render(<input aria-label="Chat input" />)
    renderDrawer(onOpenChange)
    await flushOutsideListeners()

    await user.click(screen.getByRole("textbox", { name: "Chat input" }))
    await user.keyboard("{Escape}")

    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("keeps the draft on Escape while focus is inside the drawer", async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    renderDrawer(onOpenChange)

    await user.click(screen.getByRole("textbox", { name: editorLabel }))
    await user.keyboard("{Escape}")

    expect(onOpenChange).not.toHaveBeenCalled()
    expect(
      screen.getByRole("textbox", { name: editorLabel })
    ).toBeInTheDocument()
  })

  it("closes through the close button", () => {
    const onOpenChange = jest.fn()
    renderDrawer(onOpenChange)

    fireEvent.click(screen.getByRole("button", { name: /close/i }))

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe("saving", () => {
  it("saves the rich text draft and closes", async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    const onSave = jest.fn()
    renderLongTextDrawer(onOpenChange, onSave)

    const editor = screen.getByRole("textbox", { name: "Rich text editor" })
    await user.clear(editor)
    await user.type(editor, "updated")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave).toHaveBeenCalledWith("updated")
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("parses the JSON draft on save and blocks invalid JSON", async () => {
    const onOpenChange = jest.fn()
    const onSave = jest.fn()
    renderJsonDrawer(onOpenChange, onSave)

    const editor = screen.getByRole("textbox", { name: "JSON editor" })
    fireEvent.change(editor, { target: { value: "{" } })
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()

    fireEvent.change(editor, { target: { value: '{"beta": 2}' } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave).toHaveBeenCalledWith({ beta: 2 })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe("under a CaseValueDrawerProvider", () => {
  function TwoFields() {
    const [openField, setOpenField] = useState<"notes" | "payload" | null>(null)
    return (
      <CaseValueDrawerProvider>
        <button type="button" onClick={() => setOpenField("notes")}>
          Open notes
        </button>
        <button type="button" onClick={() => setOpenField("payload")}>
          Open payload
        </button>
        <LongTextFieldDrawer
          open={openField === "notes"}
          onOpenChange={(open) => setOpenField(open ? "notes" : null)}
          fieldLabel="Analyst notes"
          initialValue=""
          onSave={jest.fn()}
        />
        <JsonFieldDrawer
          open={openField === "payload"}
          onOpenChange={(open) => setOpenField(open ? "payload" : null)}
          fieldLabel="Raw payload"
          initialValue={null}
          onSave={jest.fn()}
        />
      </CaseValueDrawerProvider>
    )
  }

  it("shares one drawer and swaps its content", async () => {
    const user = userEvent.setup()
    render(<TwoFields />)

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Open notes" }))
    expect(await screen.findByText("Analyst notes")).toBeInTheDocument()
    expect(
      await screen.findByRole("textbox", { name: "Rich text editor" })
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Open payload" }))
    expect(await screen.findByText("Raw payload")).toBeInTheDocument()
    expect(
      await screen.findByRole("textbox", { name: "JSON editor" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("textbox", { name: "Rich text editor" })
    ).not.toBeInTheDocument()
    expect(screen.getAllByRole("dialog")).toHaveLength(1)
  })

  it("tells the first owner to close when another takes the drawer", async () => {
    const user = userEvent.setup()
    const onNotesOpenChange = jest.fn()

    function Fields() {
      const [payloadOpen, setPayloadOpen] = useState(false)
      return (
        <CaseValueDrawerProvider>
          <button type="button" onClick={() => setPayloadOpen(true)}>
            Open payload
          </button>
          <LongTextFieldDrawer
            open={true}
            onOpenChange={onNotesOpenChange}
            fieldLabel="Analyst notes"
            initialValue=""
            onSave={jest.fn()}
          />
          <JsonFieldDrawer
            open={payloadOpen}
            onOpenChange={setPayloadOpen}
            fieldLabel="Raw payload"
            initialValue={null}
            onSave={jest.fn()}
          />
        </CaseValueDrawerProvider>
      )
    }
    render(<Fields />)

    await user.click(screen.getByRole("button", { name: "Open payload" }))

    expect(onNotesOpenChange).toHaveBeenCalledWith(false)
  })

  it("closes the drawer from its close button", async () => {
    const user = userEvent.setup()
    render(<TwoFields />)

    await user.click(screen.getByRole("button", { name: "Open notes" }))
    await screen.findByRole("textbox", { name: "Rich text editor" })
    await user.click(screen.getByRole("button", { name: /close/i }))

    expect(
      screen.queryByRole("textbox", { name: "Rich text editor" })
    ).not.toBeInTheDocument()
  })
})
