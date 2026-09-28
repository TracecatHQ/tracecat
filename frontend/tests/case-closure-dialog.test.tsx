/**
 * @jest-environment jsdom
 */

import { act, fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { CaseFieldReadMinimal } from "@/client"
import { CaseClosureDialog } from "@/components/cases/case-closure-dialog"

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

const requiredFields: CaseFieldReadMinimal[] = [
  {
    id: "closure_reason",
    display_name: "Closure reason",
    type: "TEXT",
    description: "Why the case was closed",
    nullable: true,
    default: null,
    reserved: false,
    options: null,
    kind: null,
    required_on_closure: true,
  },
]

/**
 * Radix registers its outside-pointer listener inside a `setTimeout(..., 0)`,
 * so the dismissal assertions are only meaningful once that timer has run.
 */
async function flushOutsideListeners() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

function renderClosureDialog(onOpenChange: jest.Mock) {
  return render(
    <CaseClosureDialog
      open={true}
      onOpenChange={onOpenChange}
      targetStatus="closed"
      requiredFields={requiredFields}
      requiredDropdowns={[]}
      currentFieldValues={{ closure_reason: "False positive" }}
      onSubmit={jest.fn()}
    />
  )
}

describe("CaseClosureDialog", () => {
  it("has no cancel button", () => {
    renderClosureDialog(jest.fn())

    expect(
      screen.queryByRole("button", { name: /cancel/i })
    ).not.toBeInTheDocument()
  })

  it("does not close on a backdrop click", async () => {
    const onOpenChange = jest.fn()
    renderClosureDialog(onOpenChange)
    await flushOutsideListeners()

    fireEvent.pointerDown(document.body)
    fireEvent.click(document.body)

    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("does not close on Escape", async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    renderClosureDialog(onOpenChange)

    await user.keyboard("{Escape}")

    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("closes through the close button", () => {
    const onOpenChange = jest.fn()
    renderClosureDialog(onOpenChange)

    // Exact name: the footer submit button is also named "Close case".
    fireEvent.click(screen.getByRole("button", { name: "Close" }))

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
