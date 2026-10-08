import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ComponentProps } from "react"
import { useForm } from "react-hook-form"
import { AgentPresetToolsList } from "@/components/agents/agent-preset-tools-list"
import { Form } from "@/components/ui/form"
import type { PresetToolFields } from "@/lib/agent-preset-tools"
import { mcpIntegration, registryTool } from "./fixtures/agent-preset-tools"

jest.mock("@/components/icons", () => ({
  getIcon: () => null,
  getMcpProviderIconId: (id: string) => id,
  ProviderIcon: () => null,
}))

const actions = [
  registryTool("tools.test.first", { display_group: "Test tools" }),
  registryTool("tools.test.second"),
  registryTool("tools.test.third", {
    origin: "git+ssh://git@example.com/registry",
    display_group: "Custom tools",
  }),
]
const defaults: PresetToolFields = {
  actions: [actions[0].action, actions[1].action],
  mcpIntegrations: [],
  namespaces: [],
  toolApprovals: [{ tool: actions[0].action, allow: true }],
}

function TestForm({
  values = defaults,
  show = true,
  isSaving = false,
  listProps,
  onWrite = jest.fn(),
}: {
  values?: PresetToolFields
  show?: boolean
  isSaving?: boolean
  listProps?: Partial<ComponentProps<typeof AgentPresetToolsList>>
  onWrite?: jest.Mock
}) {
  const form = useForm<PresetToolFields>({ defaultValues: values })
  const setValue: typeof form.setValue = (name, value, options) => {
    onWrite(name, value, options)
    form.setValue(name, value, options)
  }
  return (
    <Form {...form} setValue={setValue}>
      {show && (
        <AgentPresetToolsList
          registryActions={actions}
          mcpIntegrations={[
            mcpIntegration({
              tools: [
                { name: "enabled", requires_approval: true },
                { name: "disabled", enabled: false, requires_approval: true },
              ],
            }),
            mcpIntegration({
              id: "stdio",
              name: "Stdio",
              server_type: "stdio",
              tools: [{ name: "tool", requires_approval: true }],
            }),
          ]}
          isSaving={isSaving}
          {...listProps}
        />
      )}
      <output data-testid="dirty">{String(form.formState.isDirty)}</output>
      <output data-testid="values">{JSON.stringify(form.watch())}</output>
    </Form>
  )
}

function values(): PresetToolFields {
  return JSON.parse(screen.getByTestId("values").textContent ?? "{}")
}

it("counts a namespace across registry sources and toggles Ask to Auto and back", async () => {
  const user = userEvent.setup()
  render(<TestForm />)
  expect(screen.getByText("2 of 3")).toBeInTheDocument()
  expect(screen.getByText("1 need approval")).toBeInTheDocument()
  await user.click(
    screen.getByRole("button", { name: /Test tools tools.test/ })
  )
  await user.click(
    screen.getByRole("button", {
      name: "Require approval for tools.test.first",
    })
  )
  expect(values().toolApprovals).toEqual([])
  await user.click(
    screen.getByRole("button", {
      name: "Require approval for tools.test.first",
    })
  )
  expect(values().toolApprovals).toEqual([
    { tool: "tools.test.first", allow: true },
  ])
})

it("removes all tools in a namespace and their active rules, retaining false entries", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      values={{
        ...defaults,
        toolApprovals: [
          ...defaults.toolApprovals,
          { tool: "tools.test.second", allow: false },
        ],
      }}
    />
  )
  await user.click(screen.getByRole("button", { name: "Manage Test tools" }))
  await user.click(screen.getByRole("menuitem", { name: "Remove all" }))
  expect(values().actions).toEqual([])
  expect(values().toolApprovals).toEqual([
    { tool: "tools.test.second", allow: false },
  ])
})

it("applies group approvals without changing stored action order", async () => {
  const user = userEvent.setup()
  render(<TestForm />)
  await user.click(screen.getByRole("button", { name: "Manage Test tools" }))
  await user.click(
    screen.getByRole("menuitem", { name: "Require approval for all" })
  )
  expect(screen.getByText("2 need approval")).toBeInTheDocument()
  expect(values().actions).toEqual(defaults.actions)
  await user.click(screen.getByRole("button", { name: "Manage Test tools" }))
  await user.click(
    screen.getByRole("menuitem", { name: "Run all automatically" })
  )
  expect(values().toolApprovals).toEqual([])
})

it("renders other rules and unavailable tools, preserving invisible false rules", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      values={{
        ...defaults,
        actions: ["missing.tool"],
        toolApprovals: [
          { tool: "skill.test", allow: true },
          { tool: "mcp.test", allow: true },
          { tool: "hidden.rule", allow: false },
        ],
      }}
    />
  )
  expect(screen.getByText("Unavailable")).toBeInTheDocument()
  expect(screen.getByText("Other approval rules")).toBeInTheDocument()
  expect(screen.queryByText("hidden.rule")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Remove missing.tool" }))
  await user.click(screen.getByRole("button", { name: "Remove skill.test" }))
  await user.click(
    screen.getByRole("button", { name: "Require approval for mcp.test" })
  )
  expect(values().actions).toEqual([])
  expect(values().toolApprovals).toEqual([
    { tool: "hidden.rule", allow: false },
  ])
})

it("shows the prefix filter notice and clears only namespaces", async () => {
  const user = userEvent.setup()
  render(
    <TestForm values={{ ...defaults, namespaces: ["tools.test.first"] }} />
  )
  expect(
    screen.getByText("1 listed tool blocked by the stored namespace filter")
  ).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Clear" }))
  expect(values()).toEqual({ ...defaults, namespaces: [] })
})

it("preserves changes when the tab unmounts and remounts", async () => {
  const user = userEvent.setup()
  const { rerender } = render(<TestForm />)
  await user.click(
    screen.getByRole("button", { name: /Test tools tools.test/ })
  )
  await user.click(
    screen.getByRole("button", {
      name: "Require approval for tools.test.second",
    })
  )
  await user.click(
    screen.getByRole("button", { name: "Remove tools.test.first" })
  )
  rerender(<TestForm show={false} />)
  expect(values().actions).toEqual(["tools.test.second"])
  rerender(<TestForm />)
  await user.click(
    screen.getByRole("button", { name: /Test tools tools.test/ })
  )
  expect(
    screen.getByRole("button", {
      name: "Require approval for tools.test.second",
    })
  ).toBeInTheDocument()
})

it("counts enabled remote MCP approvals and removes the whole integration", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      values={{
        ...defaults,
        actions: [],
        toolApprovals: [],
        mcpIntegrations: ["mcp-test", "stdio"],
      }}
    />
  )
  expect(screen.getAllByText("1 need approval")).toHaveLength(1)
  expect(screen.getAllByText("All tools")).toHaveLength(2)
  await user.click(screen.getByRole("button", { name: "Remove Test MCP" }))
  expect(values().mcpIntegrations).toEqual(["stdio"])
})

it("filters in place and disables edits during save", async () => {
  const user = userEvent.setup()
  render(<TestForm isSaving />)
  expect(screen.getByRole("button", { name: "Add tools" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "Search allowed tools" }))
  await user.type(
    screen.getByRole("textbox", { name: "Filter allowed tools" }),
    "first"
  )
  expect(
    screen.getByRole("button", {
      name: "Require approval for tools.test.first",
    })
  ).toBeDisabled()
  expect(
    screen.queryByRole("button", {
      name: "Require approval for tools.test.second",
    })
  ).not.toBeInTheDocument()
  expect(
    within(
      screen.getByRole("button", { name: /Test tools tools.test/ })
    ).getByText("Test tools")
  ).toBeInTheDocument()
})

it.each([
  { registryLoading: true, registryActions: undefined },
  { mcpLoading: true, mcpIntegrations: undefined },
])(
  "waits for catalog data without exposing unavailable tools or raw MCP ids (%j)",
  (listProps) => {
    render(
      <TestForm
        listProps={listProps}
        values={{
          ...defaults,
          actions: ["missing.tool"],
          mcpIntegrations: ["mcp-test"],
        }}
      />
    )
    expect(screen.getByText("Loading tools...")).toBeInTheDocument()
    expect(screen.queryByText("Unavailable")).not.toBeInTheDocument()
    expect(screen.queryByText("mcp-test")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Remove/ })
    ).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add tools" })).toBeDisabled()
  }
)

it("shows a failed catalog instead of unavailable tools, even when data is undefined", () => {
  render(
    <TestForm
      listProps={{
        registryActions: undefined,
        registryLoading: true,
        toolsLoadError: true,
      }}
    />
  )
  expect(screen.getByText("Tools could not be loaded.")).toBeInTheDocument()
  expect(screen.queryByText("Loading tools...")).not.toBeInTheDocument()
  expect(screen.queryByText("Unavailable")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Add tools" })).toBeDisabled()
})

it("shows skill-contributed policy tools once with approval controls and no removal", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      listProps={{
        effectiveActions: [
          "tools.test.first",
          "tools.test.third",
          "skill.unknown",
        ],
      }}
      values={{
        ...defaults,
        toolApprovals: [{ tool: "tools.test.third", allow: true }],
      }}
    />
  )
  const group = screen.getByText("From skills").parentElement as HTMLElement
  expect(within(group).getByText("third")).toBeInTheDocument()
  expect(within(group).getByText("skill.unknown")).toBeInTheDocument()
  expect(
    within(group).queryByRole("button", { name: /Remove/ })
  ).not.toBeInTheDocument()
  expect(screen.queryByText("Other approval rules")).not.toBeInTheDocument()
  await user.click(
    within(group).getByRole("button", {
      name: "Require approval for skill.unknown",
    })
  )
  expect(values().toolApprovals).toContainEqual({
    tool: "skill.unknown",
    allow: true,
  })
  await user.click(
    within(group).getByRole("button", {
      name: "Require approval for tools.test.third",
    })
  )
  expect(values().toolApprovals).not.toContainEqual({
    tool: "tools.test.third",
    allow: true,
  })
})

it("does not invent skill tools when the saved policy is absent", () => {
  render(<TestForm />)
  expect(screen.queryByText("From skills")).not.toBeInTheDocument()
})

it("keeps no-op bulk Auto clean and restores cleanliness after Ask then Auto", async () => {
  const user = userEvent.setup()
  const onWrite = jest.fn()
  const original = [{ tool: "untouched", allow: false }]
  render(
    <TestForm
      onWrite={onWrite}
      values={{ ...defaults, toolApprovals: original }}
    />
  )
  await user.click(screen.getByRole("button", { name: "Manage Test tools" }))
  await user.click(
    screen.getByRole("menuitem", { name: "Run all automatically" })
  )
  expect(onWrite).not.toHaveBeenCalled()
  expect(screen.getByTestId("dirty")).toHaveTextContent("false")
  await user.click(
    screen.getByRole("button", { name: /Test tools tools.test/ })
  )
  const toggle = screen.getByRole("button", {
    name: "Require approval for tools.test.first",
  })
  await user.click(toggle)
  expect(toggle).toHaveAttribute("aria-pressed", "true")
  expect(screen.getByTestId("dirty")).toHaveTextContent("true")
  await user.click(toggle)
  expect(toggle).toHaveAttribute("aria-pressed", "false")
  expect(values().toolApprovals).toEqual(original)
  // React Hook Form compares the restored array deeply, so a new reference is clean.
  expect(screen.getByTestId("dirty")).toHaveTextContent("false")
})

it("hides filtered group headings and shows one no-match line", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      listProps={{ effectiveActions: ["skill.tool"] }}
      values={{
        ...defaults,
        actions: [...defaults.actions, "missing.tool"],
        toolApprovals: [{ tool: "other.rule", allow: true }],
      }}
    />
  )
  const count = screen.getByRole("heading", {
    name: "Tools",
  }).nextElementSibling
  expect(count).toHaveTextContent("2")
  await user.click(screen.getByRole("button", { name: "Search allowed tools" }))
  await user.type(
    screen.getByRole("textbox", { name: "Filter allowed tools" }),
    "no-match"
  )
  for (const label of [
    "Unavailable",
    "Other approval rules",
    "From skills",
    "Test tools",
  ])
    expect(screen.queryByText(label)).not.toBeInTheDocument()
  expect(screen.getAllByText("No matching tools")).toHaveLength(1)
})
