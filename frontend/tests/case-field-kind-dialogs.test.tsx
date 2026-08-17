/**
 * @jest-environment jsdom
 */

import { act, fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  FIELD_EDITOR_DIALOG_CLASS,
  JsonFieldDialog,
  LongTextFieldDialog,
} from "@/components/cases/case-field-kind-dialogs"
import { Dialog, DialogContent } from "@/components/ui/dialog"

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

function renderLongTextDialog(onOpenChange: jest.Mock) {
  return render(
    <LongTextFieldDialog
      open={true}
      onOpenChange={onOpenChange}
      fieldLabel="Analyst notes"
      initialValue="<p>hello</p>"
      onSave={jest.fn()}
    />
  )
}

function renderJsonDialog(onOpenChange: jest.Mock) {
  return render(
    <JsonFieldDialog
      open={true}
      onOpenChange={onOpenChange}
      fieldLabel="Raw payload"
      initialValue={{ alpha: 1 }}
      onSave={jest.fn()}
    />
  )
}

const dialogCases: Array<{
  name: string
  renderDialog: (onOpenChange: jest.Mock) => void
}> = [
  { name: "LongTextFieldDialog", renderDialog: renderLongTextDialog },
  { name: "JsonFieldDialog", renderDialog: renderJsonDialog },
]

describe.each(dialogCases)("$name", ({ renderDialog }) => {
  it("has no cancel button", () => {
    renderDialog(jest.fn())

    expect(
      screen.queryByRole("button", { name: /cancel/i })
    ).not.toBeInTheDocument()
  })

  it("does not close on a backdrop click", async () => {
    const onOpenChange = jest.fn()
    renderDialog(onOpenChange)
    await flushOutsideListeners()

    clickOutside()

    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("does not close on Escape", async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    renderDialog(onOpenChange)

    await user.keyboard("{Escape}")

    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("closes through the close button", async () => {
    const onOpenChange = jest.fn()
    renderDialog(onOpenChange)

    fireEvent.click(screen.getByRole("button", { name: /close/i }))

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe("control dialog without the dismissal guards", () => {
  // These two tests prove the assertions above are not vacuous: the same
  // interactions DO dismiss a dialog that omits `nonDismissableDialogProps`.
  function renderControlDialog(onOpenChange: jest.Mock) {
    render(
      <Dialog open={true} onOpenChange={onOpenChange}>
        <DialogContent title="Control dialog" aria-describedby={undefined}>
          body
        </DialogContent>
      </Dialog>
    )
  }

  it("closes on a backdrop click", async () => {
    const onOpenChange = jest.fn()
    renderControlDialog(onOpenChange)
    await flushOutsideListeners()

    clickOutside()

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("closes on Escape", async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    renderControlDialog(onOpenChange)

    await user.keyboard("{Escape}")

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe("field editor dialog shell", () => {
  it("gives both dialogs an identical responsive shell", () => {
    const longText = render(
      <LongTextFieldDialog
        open={true}
        onOpenChange={jest.fn()}
        fieldLabel="Analyst notes"
        initialValue=""
        onSave={jest.fn()}
      />
    )
    const longTextClassName = screen.getByRole("dialog").className
    longText.unmount()

    render(
      <JsonFieldDialog
        open={true}
        onOpenChange={jest.fn()}
        fieldLabel="Raw payload"
        initialValue={null}
        onSave={jest.fn()}
      />
    )
    const jsonClassName = screen.getByRole("dialog").className

    expect(jsonClassName).toBe(longTextClassName)
    expect(longTextClassName).toContain("max-w-4xl")
    expect(longTextClassName).toContain("h-[70vh]")
    expect(FIELD_EDITOR_DIALOG_CLASS).toContain("max-w-4xl")
    expect(FIELD_EDITOR_DIALOG_CLASS).toContain("h-[70vh]")
  })
})
