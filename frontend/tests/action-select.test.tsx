import { fireEvent, render, screen } from "@testing-library/react"
import type { ControllerRenderProps, FieldValues } from "react-hook-form"
import { ActionSelect } from "@/components/chat/action-select"

function makeField(value = "") {
  return {
    value,
    onChange: jest.fn(),
    onBlur: jest.fn(),
    name: "tool",
    ref: jest.fn(),
  } as unknown as ControllerRenderProps<FieldValues>
}

describe("ActionSelect", () => {
  beforeAll(() => {
    global.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  })

  it("keeps command item values safe when action values contain JSON", () => {
    const field = makeField()
    const unsafeValue =
      'mcp: {"mode": "per_app_review", "client_id": "example-client-id"}'

    render(
      <ActionSelect
        field={field}
        suggestions={[
          {
            id: "unsafe-mcp-action",
            label: "Unsafe MCP action",
            value: unsafeValue,
            description: "MCP tool",
          },
        ]}
      />
    )

    fireEvent.click(screen.getByRole("combobox"))

    expect(screen.getByText("Unsafe MCP action")).toBeInTheDocument()
    expect(() => {
      document.querySelector(`[cmdk-item=""][data-value="${unsafeValue}"]`)
    }).toThrow()
    expect(() => {
      document.querySelector('[cmdk-item=""][data-value="0"]')
    }).not.toThrow()

    fireEvent.click(screen.getByText("Unsafe MCP action"))

    expect(field.onChange).toHaveBeenCalledWith(unsafeValue)
  })
})
