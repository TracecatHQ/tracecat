import { fireEvent, render, screen } from "@testing-library/react"
import { MultiTagCommandInput } from "@/components/tags-input"

describe("MultiTagCommandInput", () => {
  beforeAll(() => {
    global.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  })

  it("invokes the locked suggestion handler when clicking the action row", () => {
    const handleLockedSelect = jest.fn()

    render(
      <MultiTagCommandInput
        suggestions={[
          {
            id: "locked-action",
            label: "Locked action",
            value: "tools.locked.action",
            description: "Premium tool",
            locked: true,
            onSelect: handleLockedSelect,
          },
        ]}
        searchKeys={["value", "label", "description", "group"]}
      />
    )

    const input = screen.getByPlaceholderText("Add tags...")
    fireEvent.focus(input)
    fireEvent.mouseDown(screen.getByText("Locked action"))
    fireEvent.click(screen.getByText("Locked action"))

    expect(handleLockedSelect).toHaveBeenCalledTimes(1)
  })

  it("keeps command item values safe when suggestion values contain JSON", () => {
    const handleChange = jest.fn()
    const unsafeValue =
      'mcp: {"mode": "per_app_review", "client_id": "example-client-id"}'

    render(
      <MultiTagCommandInput
        onChange={handleChange}
        suggestions={[
          {
            id: "unsafe-mcp-action",
            label: "Unsafe MCP action",
            value: unsafeValue,
            description: "MCP tool",
          },
        ]}
        searchKeys={["value", "label", "description", "group"]}
      />
    )

    const input = screen.getByPlaceholderText("Add tags...")
    fireEvent.focus(input)

    expect(screen.getByText("Unsafe MCP action")).toBeInTheDocument()
    expect(() => {
      document.querySelector(`[cmdk-item=""][data-value="${unsafeValue}"]`)
    }).toThrow()
    expect(() => {
      document.querySelector('[cmdk-item=""][data-value="0"]')
    }).not.toThrow()

    fireEvent.mouseDown(screen.getByText("Unsafe MCP action"))
    fireEvent.click(screen.getByText("Unsafe MCP action"))

    expect(handleChange).toHaveBeenCalledWith([unsafeValue])
  })
})
