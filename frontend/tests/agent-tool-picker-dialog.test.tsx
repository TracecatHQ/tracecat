import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { AgentToolPickerDialog } from "@/components/agents/agent-tool-picker-dialog"
import { Form } from "@/components/ui/form"
import {
  buildToolIndex,
  type PresetToolFields,
  type ToolIndex,
} from "@/lib/agent-preset-tools"
import {
  largeToolCatalog,
  mcpIntegration,
  registryTool,
} from "./fixtures/agent-preset-tools"

const actions = [
  registryTool("tools.alpha.first", { display_group: "Alpha" }),
  registryTool("tools.alpha.second"),
  registryTool("tools.beta.third", { display_group: "Beta" }),
]
const index = buildToolIndex(actions, [
  mcpIntegration({ tools: [{ name: "Read only tool" }] }),
])
const defaults: PresetToolFields = {
  actions: [],
  mcpIntegrations: [],
  namespaces: [],
  toolApprovals: [],
}
let originalHeight: PropertyDescriptor | undefined
let originalWidth: PropertyDescriptor | undefined
let originalScrollTo: typeof HTMLElement.prototype.scrollTo
beforeAll(() => {
  originalHeight = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetHeight"
  )
  originalWidth = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetWidth"
  )
  originalScrollTo = HTMLElement.prototype.scrollTo
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get: () => 440,
  })
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get: () => 1000,
  })
  HTMLElement.prototype.scrollTo = function scrollTo(
    options?: ScrollToOptions | number
  ) {
    if (typeof options !== "number") this.scrollTop = options?.top ?? 0
    this.dispatchEvent(new Event("scroll"))
  }
})
afterAll(() => {
  if (originalHeight)
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", originalHeight)
  if (originalWidth)
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", originalWidth)
  HTMLElement.prototype.scrollTo = originalScrollTo
})

function TestForm({
  values = defaults,
  catalog = index,
  maxTools = 128,
  skillActions,
  onWrite = jest.fn(),
}: {
  values?: PresetToolFields
  catalog?: ToolIndex
  maxTools?: number | null
  skillActions?: string[]
  onWrite?: jest.Mock
}) {
  const form = useForm<PresetToolFields>({ defaultValues: values })
  const [open, setOpen] = useState(true)
  const setValue: typeof form.setValue = (name, value, options) => {
    onWrite(name, value, options)
    form.setValue(name, value, options)
  }
  return (
    <Form {...form} setValue={setValue}>
      <AgentToolPickerDialog
        index={catalog}
        open={open}
        onOpenChange={setOpen}
        maxTools={maxTools}
        skillActions={skillActions}
      />
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <output data-testid="values">{JSON.stringify(form.watch())}</output>
      <output data-testid="dirty">{String(form.formState.isDirty)}</output>
    </Form>
  )
}

function values(): PresetToolFields {
  return JSON.parse(screen.getByTestId("values").textContent ?? "{}")
}

it("virtualizes 1,500 actions with fewer than 40 mounted options", async () => {
  const catalog = buildToolIndex(largeToolCatalog())
  render(<TestForm catalog={catalog} />)
  await screen.findAllByRole("option")
  expect(screen.getAllByRole("option").length).toBeGreaterThan(0)
  expect(screen.getAllByRole("option").length).toBeLessThan(40)
  expect(screen.getByRole("combobox", { name: "Search tools" })).toHaveFocus()
})

it("searches across namespaces even while viewing one source", async () => {
  const user = userEvent.setup()
  render(<TestForm />)
  await user.click(screen.getByRole("button", { name: "Alpha 2" }))
  await user.type(
    screen.getByRole("combobox", { name: "Search tools" }),
    "third"
  )
  expect(
    await screen.findByRole("option", { name: /third/ })
  ).toBeInTheDocument()
  expect(screen.getByText("1 result")).toBeInTheDocument()
})

it("uses a tri-state namespace checkbox and applies changes only on Done", async () => {
  const user = userEvent.setup()
  const onWrite = jest.fn()
  render(
    <TestForm
      values={{ ...defaults, actions: ["tools.alpha.first"] }}
      onWrite={onWrite}
    />
  )
  await user.click(screen.getByRole("button", { name: "Alpha 1/2" }))
  const checkbox = screen.getByRole("checkbox", { name: "Select all in Alpha" })
  expect(checkbox).toHaveAttribute("aria-checked", "mixed")
  await user.click(checkbox)
  expect(checkbox).toBeChecked()
  expect(onWrite).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(onWrite).toHaveBeenCalledTimes(1)
  expect(onWrite).toHaveBeenCalledWith(
    "actions",
    ["tools.alpha.first", "tools.alpha.second"],
    { shouldDirty: true }
  )
})

it("handles arrows, Enter, paging and Space in the list without eating query spaces", async () => {
  const user = userEvent.setup()
  render(<TestForm />)
  await user.click(screen.getByRole("button", { name: "Alpha 2" }))
  const search = screen.getByRole("combobox", { name: "Search tools" })
  expect(search).toHaveFocus()
  await user.keyboard("{ArrowDown}{Enter}")
  expect(screen.getByRole("option", { name: /second/ })).toHaveAttribute(
    "aria-selected",
    "true"
  )
  await user.type(search, " ")
  expect(search).toHaveValue(" ")
  expect(screen.getByText("1 of 128 tools")).toBeInTheDocument()
  const list = screen.getByRole("listbox")
  list.focus()
  fireEvent.keyDown(list, { key: "Home" })
  fireEvent.keyDown(list, { key: " " })
  expect(screen.getByRole("option", { name: /first/ })).toHaveAttribute(
    "aria-selected",
    "true"
  )
  fireEvent.keyDown(list, { key: "PageDown" })
  fireEvent.keyDown(list, { key: "Enter" })
  expect(screen.getByRole("option", { name: /second/ })).toHaveAttribute(
    "aria-selected",
    "false"
  )
  fireEvent.keyDown(list, { key: "PageUp" })
  fireEvent.keyDown(list, { key: "ArrowUp" })
  fireEvent.keyDown(list, { key: "End" })
  expect(list).toHaveAttribute(
    "aria-activedescendant",
    screen.getByRole("option", { name: /second/ }).id
  )
})

it("preserves unknown keys and ordering and leaves the form clean on no-change Done", async () => {
  const user = userEvent.setup()
  const onWrite = jest.fn()
  const initial = {
    ...defaults,
    actions: ["tools.beta.third", "unknown", "tools.alpha.first"],
    mcpIntegrations: ["unknown-mcp"],
    toolApprovals: [{ tool: "untouched", allow: false }],
  }
  render(<TestForm values={initial} onWrite={onWrite} />)
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(onWrite).not.toHaveBeenCalled()
  expect(screen.getByTestId("dirty")).toHaveTextContent("false")
  expect(values()).toEqual(initial)
  await user.click(screen.getByRole("button", { name: "Open" }))
  await user.click(await screen.findByRole("option", { name: /second/ }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(values().actions).toEqual([...initial.actions, "tools.alpha.second"])
})

it("removes active approvals for deselected actions once, preserving false and other rules", async () => {
  const user = userEvent.setup()
  const onWrite = jest.fn()
  render(
    <TestForm
      onWrite={onWrite}
      values={{
        ...defaults,
        actions: ["tools.alpha.first"],
        toolApprovals: [
          { tool: "tools.alpha.first", allow: true },
          { tool: "tools.alpha.second", allow: false },
          { tool: "skill.test", allow: true },
        ],
      }}
    />
  )
  await user.click(await screen.findByRole("option", { name: /first/ }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(onWrite.mock.calls.map(([name]) => name)).toEqual([
    "actions",
    "toolApprovals",
  ])
  expect(values().toolApprovals).toEqual([
    { tool: "tools.alpha.second", allow: false },
    { tool: "skill.test", allow: true },
  ])
})

it("selects an entire MCP integration and shows its tools read-only", async () => {
  const user = userEvent.setup()
  render(<TestForm />)
  await user.click(screen.getByRole("button", { name: "Test MCP 1" }))
  expect(screen.getByText("Read only tool")).toBeInTheDocument()
  expect(
    within(screen.getByRole("listbox")).queryByText("Read only tool")
  ).not.toBeInTheDocument()
  expect(screen.getAllByRole("option")).toHaveLength(1)
  await user.click(screen.getByRole("option", { name: /Test MCP/ }))
  await user.click(screen.getByRole("button", { name: "Selected 1" }))
  expect(screen.getAllByRole("option")).toHaveLength(1)
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(values().mcpIntegrations).toEqual(["mcp-test"])
})

it("discards local edits on Cancel and Escape", async () => {
  const user = userEvent.setup()
  const onWrite = jest.fn()
  render(<TestForm onWrite={onWrite} />)
  await user.click(await screen.findByRole("option", { name: /first/ }))
  await user.click(screen.getByRole("button", { name: "Cancel" }))
  expect(onWrite).not.toHaveBeenCalled()
  expect(values()).toEqual(defaults)
  await user.click(screen.getByRole("button", { name: "Open" }))
  expect(await screen.findByRole("option", { name: /first/ })).toHaveAttribute(
    "aria-selected",
    "false"
  )
  await user.keyboard("{Escape}")
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  )
  expect(onWrite).not.toHaveBeenCalled()
})

it("virtualizes the source rail and omits empty section labels", async () => {
  const catalog = buildToolIndex(largeToolCatalog())
  render(<TestForm catalog={catalog} />)
  const rail = screen.getByRole("navigation", { name: "Tool sources" })
  const buttons = await within(rail).findAllByRole("button")
  expect(catalog.groups).toHaveLength(125)
  expect(buttons.length).toBeGreaterThan(2)
  expect(buttons.length).toBeLessThan(30)
  expect(within(rail).queryByText("MCP servers")).not.toBeInTheDocument()
  expect(within(rail).queryByText("Custom registry")).not.toBeInTheDocument()
})

it("skips headers and keeps the active option mounted across a large catalog", async () => {
  render(<TestForm catalog={buildToolIndex(largeToolCatalog())} />)
  const search = screen.getByRole("combobox")
  function activeOption() {
    const id = search.getAttribute("aria-activedescendant")
    expect(id).toBeTruthy()
    const option = document.getElementById(id ?? "")
    expect(option).toHaveAttribute("role", "option")
    return option
  }
  await waitFor(() => expect(activeOption()).toHaveTextContent("action_0"))
  fireEvent.keyDown(search, { key: "ArrowDown" })
  expect(activeOption()).toHaveTextContent("action_1")
  fireEvent.keyDown(search, { key: "ArrowUp" })
  expect(activeOption()).toHaveTextContent("action_0")
  for (let i = 1; i <= 5; i++) {
    fireEvent.keyDown(search, { key: "PageDown" })
    await waitFor(() =>
      expect(activeOption()).toHaveTextContent(`action_${i * 10}`)
    )
  }
  for (let i = 0; i < 3; i++) fireEvent.keyDown(search, { key: "ArrowUp" })
  expect(activeOption()).toHaveTextContent("action_47")
})

it("leaves Home and End to the search caret and ignores IME Enter", async () => {
  const user = userEvent.setup()
  render(<TestForm />)
  await user.click(screen.getByRole("button", { name: "Alpha 2" }))
  const search = screen.getByRole("combobox")
  fireEvent.keyDown(search, { key: "ArrowDown" })
  const active = search.getAttribute("aria-activedescendant")
  for (const key of ["Home", "End"]) {
    expect(fireEvent.keyDown(search, { key })).toBe(true)
    expect(search).toHaveAttribute("aria-activedescendant", active)
  }
  fireEvent.keyDown(search, { key: "Enter", isComposing: true })
  expect(screen.getByRole("option", { name: /second/ })).toHaveAttribute(
    "aria-selected",
    "false"
  )
})

it("retains the numeric position after removing the active Selected row", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      values={{ ...defaults, actions: actions.map((action) => action.action) }}
    />
  )
  await user.click(screen.getByRole("button", { name: "Selected 3" }))
  const search = screen.getByRole("combobox")
  await user.keyboard("{ArrowDown}{Enter}")
  expect(
    screen.queryByRole("option", { name: /second/ })
  ).not.toBeInTheDocument()
  expect(search).toHaveAttribute(
    "aria-activedescendant",
    screen.getByRole("option", { name: /third/ }).id
  )
  await user.keyboard("{Enter}")
  expect(search).toHaveAttribute(
    "aria-activedescendant",
    screen.getByRole("option", { name: /first/ }).id
  )
})

it("counts indexed Selected rows but retains unknown selections in the footer", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      values={{
        ...defaults,
        actions: ["tools.alpha.first", "missing"],
        mcpIntegrations: ["missing-mcp"],
      }}
    />
  )
  expect(screen.getByText(/2 of 128 tools/)).toHaveTextContent(
    "2 of 128 tools · 1 MCP integration"
  )
  await user.click(screen.getByRole("button", { name: "Selected 1" }))
  expect(screen.getAllByRole("option")).toHaveLength(1)
})

it("deselects a whole MCP integration on Done", async () => {
  const user = userEvent.setup()
  const onWrite = jest.fn()
  render(
    <TestForm
      values={{ ...defaults, mcpIntegrations: ["mcp-test"] }}
      onWrite={onWrite}
    />
  )
  await user.click(screen.getByRole("button", { name: "Test MCP 1/1" }))
  await user.click(screen.getByRole("option", { name: /Test MCP/ }))
  expect(onWrite).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(onWrite).toHaveBeenCalledTimes(1)
  expect(onWrite).toHaveBeenCalledWith("mcpIntegrations", [], {
    shouldDirty: true,
  })
  expect(values().mcpIntegrations).toEqual([])
})

it.each([
  { actions: ["first"], mcp: [], expected: "1 tool selected" },
  {
    actions: ["first", "second"],
    mcp: ["one", "two"],
    expected: "2 tools selected · 2 MCP integrations",
  },
])(
  "formats the footer without a limit: $expected",
  ({ actions, mcp, expected }) => {
    render(
      <TestForm
        maxTools={null}
        skillActions={["tools.skill.extra"]}
        values={{ ...defaults, actions, mcpIntegrations: mcp }}
      />
    )
    expect(screen.getByText(expected)).toBeInTheDocument()
  }
)

it("keeps divs out of buttons in tool option rows and the source rail", () => {
  render(<TestForm />)
  const rail = screen.getByRole("navigation", { name: "Tool sources" })
  const rows = [
    ...screen.getAllByRole("option"),
    ...within(rail).getAllByRole("button"),
  ]
  for (const row of rows) {
    if (row.matches("button")) {
      expect(row.querySelector("div")).toBeNull()
    } else {
      expect(row.querySelector("button div")).toBeNull()
    }
  }
})

it("leaves actions blocked by the namespace filter out of the limit", () => {
  render(
    <TestForm
      maxTools={1}
      values={{
        ...defaults,
        actions: ["tools.alpha.first", "tools.beta.third"],
        namespaces: ["tools.alpha"],
      }}
    />
  )
  expect(screen.getByText("1 of 1 tools")).not.toHaveClass("text-rose-500")
  expect(screen.getByRole("button", { name: "Done" })).toBeEnabled()
})

it.each([
  {
    skillActions: ["tools.alpha.first", "tools.beta.third"],
    expected: "3 of 3 tools (1 from skills)",
    overLimit: false,
  },
  {
    skillActions: ["tools.beta.third", "tools.skill.extra"],
    expected: "4 of 3 tools (2 from skills) · remove 1 to continue",
    overLimit: true,
  },
])(
  "counts the union of selected and skill tools: $expected",
  ({ skillActions, expected, overLimit }) => {
    render(
      <TestForm
        maxTools={3}
        skillActions={skillActions}
        values={{
          ...defaults,
          actions: ["tools.alpha.first", "tools.alpha.second"],
        }}
      />
    )
    const footer = screen.getByText(expected)
    const done = screen.getByRole("button", { name: "Done" })
    if (overLimit) {
      expect(footer).toHaveClass("text-rose-500")
      expect(done).toBeDisabled()
    } else {
      expect(footer).not.toHaveClass("text-rose-500")
      expect(done).toBeEnabled()
    }
  }
)

it("allows group selection over the limit but requires deselection before Done", async () => {
  const user = userEvent.setup()
  render(<TestForm maxTools={1} />)
  const done = screen.getByRole("button", { name: "Done" })
  expect(done).toBeEnabled()
  await user.click(screen.getByRole("button", { name: "Alpha 2" }))
  await user.click(
    screen.getByRole("checkbox", { name: "Select all in Alpha" })
  )
  expect(screen.getByText("2 of 1 tools · remove 1 to continue")).toHaveClass(
    "text-rose-500"
  )
  expect(done).toBeDisabled()
  await user.click(done)
  expect(screen.getByRole("dialog")).toBeInTheDocument()
  expect(values().actions).toEqual([])
  await user.click(screen.getByRole("option", { name: /tools.alpha.second/ }))
  expect(screen.getByText("1 of 1 tools")).not.toHaveClass("text-rose-500")
  expect(done).toBeEnabled()
  await user.click(done)
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(values().actions).toEqual(["tools.alpha.first"])
})

it("removes an Auto override when deselecting a default-ask action", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      catalog={buildToolIndex([{ ...actions[0], requires_approval: true }])}
      values={{
        ...defaults,
        actions: [actions[0].action],
        toolApprovals: [{ tool: actions[0].action, allow: false }],
      }}
    />
  )
  await user.click(screen.getByRole("option", { name: /tools.alpha.first/ }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(values().toolApprovals).toEqual([])
})

it("keeps the limit count and approval when deselecting a skill-granted action", async () => {
  const user = userEvent.setup()
  const rule = { tool: actions[0].action, allow: true }
  render(
    <TestForm
      maxTools={1}
      skillActions={[actions[0].action]}
      values={{
        ...defaults,
        actions: [actions[0].action],
        toolApprovals: [rule],
      }}
    />
  )
  expect(screen.getByText("1 of 1 tools")).toBeInTheDocument()
  await user.click(screen.getByRole("option", { name: /tools.alpha.first/ }))
  expect(screen.getByText("1 of 1 tools (1 from skills)")).toBeInTheDocument()
  await user.click(screen.getByRole("option", { name: /tools.alpha.second/ }))
  expect(
    screen.getByText("2 of 1 tools (1 from skills) · remove 1 to continue")
  ).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Done" })).toBeDisabled()
  await user.click(screen.getByRole("option", { name: /tools.alpha.second/ }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(values().actions).toEqual([])
  expect(values().toolApprovals).toEqual([rule])
})
