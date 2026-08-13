/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { CaseFieldRead } from "@/client"
import { CustomField } from "@/components/cases/case-panel-custom-fields"

// The field dialogs pull in the rich-text and CodeMirror editors transitively.
jest.mock("@/components/cases/case-description-editor", () => ({
  CaseDescriptionEditor: () => <textarea aria-label="Rich text editor" />,
}))

jest.mock("@uiw/react-codemirror", () => ({
  __esModule: true,
  default: () => <textarea aria-label="JSON editor" />,
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

function makeField(
  overrides: Pick<CaseFieldRead, "id" | "type" | "value"> &
    Partial<CaseFieldRead>
): CaseFieldRead {
  return {
    description: "",
    nullable: true,
    default: null,
    reserved: false,
    options: null,
    kind: null,
    ...overrides,
  }
}

function renderField(customField: CaseFieldRead) {
  const updateCase = jest.fn().mockResolvedValue(undefined)
  render(<CustomField customField={customField} updateCase={updateCase} />)
  return updateCase
}

function expectNoDropdownOpened() {
  expect(screen.queryByPlaceholderText("Search...")).not.toBeInTheDocument()
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
}

describe("ClearFieldButton", () => {
  it("clears a BOOLEAN field set to true", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({ id: "is_phishing", type: "BOOLEAN", value: true })
    )

    await user.click(
      screen.getByRole("button", { name: "Clear is_phishing field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { is_phishing: null } })
    })
    expectNoDropdownOpened()
  })

  it("clears a BOOLEAN field set to false", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({ id: "is_phishing", type: "BOOLEAN", value: false })
    )

    await user.click(
      screen.getByRole("button", { name: "Clear is_phishing field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { is_phishing: null } })
    })
    expectNoDropdownOpened()
  })

  it("hides the clear button when a BOOLEAN field is already empty", () => {
    renderField(makeField({ id: "is_phishing", type: "BOOLEAN", value: null }))

    expect(
      screen.queryByRole("button", { name: "Clear is_phishing field" })
    ).not.toBeInTheDocument()
  })

  it("clears a SELECT field", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({
        id: "verdict",
        type: "SELECT",
        value: "malicious",
        options: ["malicious", "benign"],
      })
    )

    await user.click(
      screen.getByRole("button", { name: "Clear verdict field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { verdict: null } })
    })
    expectNoDropdownOpened()
  })

  it("clears a MULTI_SELECT field holding a single value", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({
        id: "tactics",
        type: "MULTI_SELECT",
        value: ["alpha"],
        options: ["alpha", "beta"],
      })
    )

    await user.click(
      screen.getByRole("button", { name: "Clear tactics field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { tactics: null } })
    })
    expectNoDropdownOpened()
  })

  it("clears a MULTI_SELECT field holding two values", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({
        id: "tactics",
        type: "MULTI_SELECT",
        value: ["alpha", "beta"],
        options: ["alpha", "beta"],
      })
    )

    await user.click(
      screen.getByRole("button", { name: "Clear tactics field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { tactics: null } })
    })
    expectNoDropdownOpened()
  })
})

describe("MULTI_SELECT toggling", () => {
  it("persists null, not an empty array, when the last option is toggled off", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({
        id: "tactics",
        type: "MULTI_SELECT",
        value: ["alpha"],
        options: ["alpha", "beta"],
      })
    )

    await user.click(screen.getByRole("combobox"))
    await screen.findByPlaceholderText("Search...")
    await user.click(screen.getByRole("option", { name: "alpha" }))

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledTimes(1)
    })
    expect(updateCase).toHaveBeenCalledWith({ fields: { tactics: null } })
  })
})
