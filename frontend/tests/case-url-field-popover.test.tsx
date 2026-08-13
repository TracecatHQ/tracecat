/**
 * @jest-environment jsdom
 */

import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  getUrlHint,
  isSafeUrl,
  UrlFieldPopover,
  type UrlFieldValue,
} from "@/components/cases/case-url-field-popover"

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

afterEach(() => {
  jest.restoreAllMocks()
})

const SAVED: UrlFieldValue = {
  url: "https://example.com/tickets/1",
  label: "Zendesk ticket",
}

/**
 * Radix registers its outside-pointer listener inside a `setTimeout(..., 0)`.
 */
async function flushOutsideListeners() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

function renderPopover(value: UrlFieldValue | null) {
  const onSave = jest.fn()
  render(
    <div>
      <button type="button" data-testid="outside">
        outside
      </button>
      <UrlFieldPopover fieldId="ticket_url" value={value} onSave={onSave} />
    </div>
  )
  return onSave
}

function labelInput() {
  return screen.getByPlaceholderText("Display label")
}

function urlInput() {
  return screen.getByPlaceholderText("https://example.com")
}

describe("UrlFieldPopover row", () => {
  it("shows the saved label", () => {
    renderPopover(SAVED)

    expect(
      screen.getByRole("button", { name: "Zendesk ticket" })
    ).toBeInTheDocument()
  })

  it("falls back to the URL when the label is empty", () => {
    renderPopover({ url: "https://example.com/tickets/1", label: "" })

    expect(
      screen.getByRole("button", { name: "https://example.com/tickets/1" })
    ).toBeInTheDocument()
  })

  it('shows "Add..." when there is no value', () => {
    renderPopover(null)

    expect(screen.getByRole("button", { name: "Add..." })).toBeInTheDocument()
  })
})

describe("UrlFieldPopover editing", () => {
  it("seeds both inputs from the saved value when opened", async () => {
    const user = userEvent.setup()
    renderPopover(SAVED)

    await user.click(screen.getByRole("button", { name: "Zendesk ticket" }))

    expect(labelInput()).toHaveValue("Zendesk ticket")
    expect(urlInput()).toHaveValue("https://example.com/tickets/1")
  })

  it("disables Apply for a relative URL", async () => {
    const user = userEvent.setup()
    renderPopover(null)

    await user.click(screen.getByRole("button", { name: "Add..." }))
    await user.type(labelInput(), "Ticket")
    await user.type(urlInput(), "/tickets/1")

    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()
    expect(
      screen.getByText("Enter a valid URL, e.g. https://example.com")
    ).toBeInTheDocument()
  })

  it("disables Apply for a javascript: URL", async () => {
    const user = userEvent.setup()
    renderPopover(null)

    await user.click(screen.getByRole("button", { name: "Add..." }))
    await user.type(labelInput(), "Ticket")
    await user.type(urlInput(), "javascript:alert(1)")

    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()
    expect(
      screen.getByText("URL must start with http:// or https://")
    ).toBeInTheDocument()
  })

  it("disables Apply when the label is blank", async () => {
    const user = userEvent.setup()
    renderPopover(null)

    await user.click(screen.getByRole("button", { name: "Add..." }))
    await user.type(urlInput(), "https://example.com/tickets/2")

    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()

    await user.type(labelInput(), "Ticket")

    expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled()
  })

  it("persists the url and label when Apply is clicked", async () => {
    const user = userEvent.setup()
    const onSave = renderPopover(null)

    await user.click(screen.getByRole("button", { name: "Add..." }))
    await user.type(labelInput(), "Ticket")
    await user.type(urlInput(), "https://example.com/tickets/2")
    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(onSave).toHaveBeenCalledWith({
      url: "https://example.com/tickets/2",
      label: "Ticket",
    })
  })

  it.each([
    ["the label input", () => labelInput()],
    ["the url input", () => urlInput()],
  ])("persists on Enter in %s", async (_name, getTarget) => {
    const user = userEvent.setup()
    const onSave = renderPopover(null)

    await user.click(screen.getByRole("button", { name: "Add..." }))
    await user.type(labelInput(), "Ticket")
    await user.type(urlInput(), "https://example.com/tickets/2")
    await user.type(getTarget(), "{Enter}")

    expect(onSave).toHaveBeenCalledWith({
      url: "https://example.com/tickets/2",
      label: "Ticket",
    })
  })

  it("trims leading and trailing whitespace before saving", async () => {
    const user = userEvent.setup()
    const onSave = renderPopover(null)

    await user.click(screen.getByRole("button", { name: "Add..." }))
    await user.type(labelInput(), "  Ticket  ")
    await user.type(urlInput(), "  https://example.com/tickets/2  ")
    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(onSave).toHaveBeenCalledWith({
      url: "https://example.com/tickets/2",
      label: "Ticket",
    })
  })
})

describe("UrlFieldPopover actions", () => {
  it("opens the saved URL in a new window", async () => {
    const user = userEvent.setup()
    const openSpy = jest.spyOn(window, "open").mockReturnValue(null)
    renderPopover(SAVED)

    await user.click(screen.getByRole("button", { name: "Zendesk ticket" }))
    await user.click(screen.getByRole("button", { name: "Open in new window" }))

    expect(openSpy).toHaveBeenCalledWith(
      "https://example.com/tickets/1",
      "_blank",
      "noopener,noreferrer"
    )
  })

  it("disables open in new window for an unsafe URL", async () => {
    const user = userEvent.setup()
    renderPopover({ url: "javascript:alert(1)", label: "Bad" })

    await user.click(screen.getByRole("button", { name: "Bad" }))

    expect(
      screen.getByRole("button", { name: "Open in new window" })
    ).toBeDisabled()
  })

  it("disables open in new window for an invalid URL", async () => {
    const user = userEvent.setup()
    renderPopover({ url: "not-a-url", label: "Bad" })

    await user.click(screen.getByRole("button", { name: "Bad" }))

    expect(
      screen.getByRole("button", { name: "Open in new window" })
    ).toBeDisabled()
  })

  it("saves null when the URL is removed", async () => {
    const user = userEvent.setup()
    const onSave = renderPopover(SAVED)

    await user.click(screen.getByRole("button", { name: "Zendesk ticket" }))
    await user.click(screen.getByRole("button", { name: "Remove URL" }))

    expect(onSave).toHaveBeenCalledWith(null)
  })
})

describe("UrlFieldPopover dismissal", () => {
  it("closes on an outside click and discards the draft", async () => {
    const user = userEvent.setup()
    const onSave = renderPopover(SAVED)

    await user.click(screen.getByRole("button", { name: "Zendesk ticket" }))
    await flushOutsideListeners()

    await user.clear(labelInput())
    await user.type(labelInput(), "Draft label")
    expect(labelInput()).toHaveValue("Draft label")

    await user.click(screen.getByTestId("outside"))

    await waitFor(() => {
      expect(
        screen.queryByPlaceholderText("Display label")
      ).not.toBeInTheDocument()
    })
    expect(onSave).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Zendesk ticket" }))

    expect(labelInput()).toHaveValue("Zendesk ticket")
    expect(urlInput()).toHaveValue("https://example.com/tickets/1")
  })
})

describe("url helpers", () => {
  it("accepts only absolute http(s) URLs", () => {
    expect(isSafeUrl("https://example.com")).toBe(true)
    expect(isSafeUrl("http://example.com")).toBe(true)
    expect(isSafeUrl("javascript:alert(1)")).toBe(false)
    expect(isSafeUrl("/tickets/1")).toBe(false)
    expect(isSafeUrl("")).toBe(false)
  })

  it("hints only once the user has typed something invalid", () => {
    expect(getUrlHint("")).toBeUndefined()
    expect(getUrlHint("https://example.com")).toBeUndefined()
    expect(getUrlHint("javascript:alert(1)")).toBe(
      "URL must start with http:// or https://"
    )
    expect(getUrlHint("/tickets/1")).toBe(
      "Enter a valid URL, e.g. https://example.com"
    )
  })
})
