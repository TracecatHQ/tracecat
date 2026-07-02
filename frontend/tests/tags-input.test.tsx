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

  describe("keyboard selection", () => {
    const suggestions = [
      {
        id: "a",
        label: "Alpha tool",
        value: "tools.alpha.run",
        description: "Alpha",
        group: "tools",
      },
      {
        id: "b",
        label: "Beta tool",
        value: "tools.beta.run",
        description: "Beta",
        group: "tools",
      },
      {
        id: "g",
        label: "Gamma tool",
        value: "tools.gamma.run",
        description: "Gamma",
        group: "tools",
      },
    ]
    const searchKeys: Array<"label" | "value" | "description" | "group"> = [
      "label",
      "value",
      "description",
      "group",
    ]

    it("selects the highlighted suggestion on Enter instead of adding raw text", () => {
      const handleChange = jest.fn()
      render(
        <MultiTagCommandInput
          onChange={handleChange}
          suggestions={suggestions}
          searchKeys={searchKeys}
          allowCustomTags
        />
      )

      const input = screen.getByPlaceholderText("Add tags...")
      fireEvent.focus(input)
      fireEvent.change(input, { target: { value: "gamma" } })
      fireEvent.keyDown(input, { key: "Enter" })

      expect(handleChange).toHaveBeenCalledWith(["tools.gamma.run"])
    })

    it("moves the highlight with arrow keys before selecting", () => {
      const handleChange = jest.fn()
      render(
        <MultiTagCommandInput
          onChange={handleChange}
          suggestions={suggestions}
          searchKeys={searchKeys}
        />
      )

      const input = screen.getByPlaceholderText("Add tags...")
      fireEvent.focus(input)
      fireEvent.keyDown(input, { key: "ArrowDown" })
      fireEvent.keyDown(input, { key: "Enter" })

      expect(handleChange).toHaveBeenCalledWith(["tools.beta.run"])
    })

    it("adds a custom tag through the explicit add row", () => {
      const handleChange = jest.fn()
      render(
        <MultiTagCommandInput
          onChange={handleChange}
          suggestions={suggestions}
          searchKeys={searchKeys}
          allowCustomTags
        />
      )

      const input = screen.getByPlaceholderText("Add tags...")
      fireEvent.focus(input)
      fireEvent.change(input, { target: { value: "tools.custom.thing" } })

      const addRow = screen.getByText('Add "tools.custom.thing"')
      fireEvent.mouseDown(addRow)
      fireEvent.click(addRow)

      expect(handleChange).toHaveBeenCalledWith(["tools.custom.thing"])
    })

    it("does nothing on Enter before typing or navigating", () => {
      const handleChange = jest.fn()
      render(
        <MultiTagCommandInput
          onChange={handleChange}
          suggestions={suggestions}
          searchKeys={searchKeys}
          allowCustomTags
        />
      )

      const input = screen.getByPlaceholderText("Add tags...")
      fireEvent.focus(input)
      fireEvent.keyDown(input, { key: "Enter" })

      expect(handleChange).not.toHaveBeenCalled()
    })

    it("keeps the dropdown open when clicking inside the input container", () => {
      render(
        <MultiTagCommandInput
          suggestions={suggestions}
          searchKeys={searchKeys}
        />
      )

      const input = screen.getByPlaceholderText("Add tags...")
      fireEvent.focus(input)
      expect(screen.getByText("Alpha tool")).toBeInTheDocument()

      // Radix treats pointerdown outside the portaled content as a dismiss;
      // a click inside the anchor container must not close the dropdown
      fireEvent.pointerDown(input)
      expect(screen.getByText("Alpha tool")).toBeInTheDocument()
    })

    it("reopens the dropdown when typing after it was dismissed", () => {
      render(
        <MultiTagCommandInput
          suggestions={suggestions}
          searchKeys={searchKeys}
        />
      )

      const input = screen.getByPlaceholderText("Add tags...")
      fireEvent.focus(input)
      expect(screen.getByText("Alpha tool")).toBeInTheDocument()

      fireEvent.blur(input)
      expect(screen.queryByText("Alpha tool")).not.toBeInTheDocument()

      fireEvent.change(input, { target: { value: "beta" } })
      expect(screen.getByText("Beta tool")).toBeInTheDocument()
    })

    it("adds a custom tag on Enter when nothing matches", () => {
      const handleChange = jest.fn()
      render(
        <MultiTagCommandInput
          onChange={handleChange}
          suggestions={suggestions}
          searchKeys={searchKeys}
          allowCustomTags
        />
      )

      const input = screen.getByPlaceholderText("Add tags...")
      fireEvent.focus(input)
      fireEvent.change(input, { target: { value: "zzzzqqqq" } })
      fireEvent.keyDown(input, { key: "Enter" })

      expect(handleChange).toHaveBeenCalledWith(["zzzzqqqq"])
    })
  })
})
