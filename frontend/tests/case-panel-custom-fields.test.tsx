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
    // The API defaults a field's display name to its identifier.
    display_name: overrides.id,
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

describe("BOOLEAN clear option", () => {
  it("clears a field set to true", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({
        id: "is_phishing",
        display_name: "Is phishing",
        type: "BOOLEAN",
        value: true,
      })
    )

    await user.click(screen.getByRole("combobox"))
    await user.click(
      await screen.findByRole("option", { name: "Clear Is phishing field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { is_phishing: null } })
    })
  })

  it("clears a field set to false", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({ id: "is_phishing", type: "BOOLEAN", value: false })
    )

    await user.click(screen.getByRole("combobox"))
    await user.click(
      await screen.findByRole("option", { name: "Clear is_phishing field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { is_phishing: null } })
    })
  })

  it("omits the clear option when the field is already empty", async () => {
    const user = userEvent.setup()
    renderField(makeField({ id: "is_phishing", type: "BOOLEAN", value: null }))

    await user.click(screen.getByRole("combobox"))
    await screen.findByRole("option", { name: "True" })

    expect(
      screen.queryByRole("option", { name: "Clear is_phishing field" })
    ).not.toBeInTheDocument()
  })
})

describe("SELECT clear option", () => {
  it("clears a field with a value", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({
        id: "verdict",
        type: "SELECT",
        value: "malicious",
        options: ["malicious", "benign"],
      })
    )

    await user.click(screen.getByRole("combobox"))
    await user.click(
      await screen.findByRole("button", { name: "Clear verdict field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { verdict: null } })
    })
  })

  it("omits the clear row when the field is already empty", async () => {
    const user = userEvent.setup()
    renderField(
      makeField({
        id: "verdict",
        type: "SELECT",
        value: null,
        options: ["malicious", "benign"],
      })
    )

    await user.click(screen.getByRole("combobox"))
    await screen.findByPlaceholderText("Search...")

    expect(
      screen.queryByRole("button", { name: "Clear verdict field" })
    ).not.toBeInTheDocument()
  })

  it("caps the option list height and hides its scrollbar", async () => {
    const user = userEvent.setup()
    renderField(
      makeField({
        id: "verdict",
        type: "SELECT",
        value: "malicious",
        options: ["malicious", "benign"],
      })
    )

    await user.click(screen.getByRole("combobox"))
    const list = await screen.findByRole("listbox")

    expect(list).toHaveClass("max-h-56", "no-scrollbar", "overflow-y-auto")
  })

  it("keeps the clear row visible when the search matches nothing", async () => {
    const user = userEvent.setup()
    renderField(
      makeField({
        id: "verdict",
        type: "SELECT",
        value: "malicious",
        options: ["malicious", "benign"],
      })
    )

    await user.click(screen.getByRole("combobox"))
    await user.type(await screen.findByPlaceholderText("Search..."), "zzz")

    expect(
      screen.queryByRole("option", { name: /malicious/ })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Clear verdict field" })
    ).toBeInTheDocument()
  })
})

describe("MULTI_SELECT clear option", () => {
  it("clears a field holding a single value", async () => {
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
    await user.click(
      await screen.findByRole("button", { name: "Clear tactics field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { tactics: null } })
    })
  })

  it("clears a field holding two values", async () => {
    const user = userEvent.setup()
    const updateCase = renderField(
      makeField({
        id: "tactics",
        type: "MULTI_SELECT",
        value: ["alpha", "beta"],
        options: ["alpha", "beta"],
      })
    )

    await user.click(screen.getByRole("combobox"))
    await user.click(
      await screen.findByRole("button", { name: "Clear tactics field" })
    )

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({ fields: { tactics: null } })
    })
  })

  it("omits the clear row when the field is already empty", async () => {
    const user = userEvent.setup()
    renderField(
      makeField({
        id: "tactics",
        type: "MULTI_SELECT",
        value: null,
        options: ["alpha", "beta"],
      })
    )

    await user.click(screen.getByRole("combobox"))
    await screen.findByPlaceholderText("Search...")

    expect(
      screen.queryByRole("button", { name: "Clear tactics field" })
    ).not.toBeInTheDocument()
  })

  it("caps the option list height and hides its scrollbar", async () => {
    const user = userEvent.setup()
    renderField(
      makeField({
        id: "tactics",
        type: "MULTI_SELECT",
        value: ["alpha"],
        options: ["alpha", "beta"],
      })
    )

    await user.click(screen.getByRole("combobox"))
    const list = await screen.findByRole("listbox")

    expect(list).toHaveClass("max-h-56", "no-scrollbar", "overflow-y-auto")
  })

  it("keeps the clear row visible when the search matches nothing", async () => {
    const user = userEvent.setup()
    renderField(
      makeField({
        id: "tactics",
        type: "MULTI_SELECT",
        value: ["alpha"],
        options: ["alpha", "beta"],
      })
    )

    await user.click(screen.getByRole("combobox"))
    await user.type(await screen.findByPlaceholderText("Search..."), "zzz")

    expect(
      screen.queryByRole("option", { name: "alpha" })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Clear tactics field" })
    ).toBeInTheDocument()
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
