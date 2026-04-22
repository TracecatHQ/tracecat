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
})
