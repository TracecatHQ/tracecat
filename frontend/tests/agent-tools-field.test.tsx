import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { type UseFormReturn, useForm } from "react-hook-form"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { AgentToolsField } from "@/components/builder/panel/agent-tools-field"
import { Form } from "@/components/ui/form"
import { TooltipProvider } from "@/components/ui/tooltip"
import { getAgentToolsFolding } from "@/lib/agent-tools-folding"
import { useBuilderRegistryActions, useListMcpIntegrations } from "@/lib/hooks"
import {
  largeToolCatalog,
  mcpIntegration,
  registryTool,
} from "./fixtures/agent-preset-tools"

jest.mock("@/lib/hooks", () => ({
  ...jest.requireActual("@/lib/hooks"),
  useBuilderRegistryActions: jest.fn(),
  useListMcpIntegrations: jest.fn(),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-test",
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))
jest.mock("@/components/editor/expression-input", () => ({
  ExpressionInput: ({
    value,
    onChange,
  }: {
    value: string
    onChange: (value: string) => void
  }) => (
    <input
      aria-label="Expression"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

const action = registryTool("tools.alpha.first", { display_group: "Alpha" })
const integration = mcpIntegration()
const agentProperties = {
  actions: {
    type: ["array", "null"],
    "x-tracecat-component": [{ component_id: "action-type", multiple: true }],
  },
  mcp_integrations: {
    type: ["array", "null"],
    "x-tracecat-component": [{ component_id: "mcp-integration" }],
  },
  tool_approvals: { type: ["object", "null"] },
}
const presetProperties = { actions: agentProperties.actions }

beforeAll(() => {
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

beforeEach(() => {
  jest.mocked(useScopeCheck).mockReturnValue(true)
  jest.mocked(useBuilderRegistryActions).mockReturnValue({
    registryActions: [action],
    registryActionsIsLoading: false,
    registryActionsError: null,
    getRegistryAction: jest.fn(),
  })
  jest.mocked(useListMcpIntegrations).mockReturnValue({
    mcpIntegrations: [integration],
    mcpIntegrationsIsLoading: false,
    mcpIntegrationsError: null,
  })
})

let currentForm: UseFormReturn<{ inputs: Record<string, unknown> }>

function TestField({
  properties = agentProperties,
  inputs = {},
}: {
  properties?: Record<string, unknown>
  inputs?: Record<string, unknown>
}) {
  const form = useForm({ defaultValues: { inputs } })
  currentForm = form
  return (
    <TooltipProvider>
      <Form {...form}>
        <AgentToolsField fieldName="inputs.actions" properties={properties} />
        <output data-testid="values">
          {JSON.stringify(form.watch("inputs"))}
        </output>
        <output data-testid="dirty">{String(form.formState.isDirty)}</output>
      </Form>
    </TooltipProvider>
  )
}

function values(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("values").textContent ?? "{}")
}

it("writes agent tools, MCP integrations and approval overrides, then clears empty siblings", async () => {
  const user = userEvent.setup()
  render(<TestField />)
  expect(screen.getByTestId("dirty")).toHaveTextContent("false")
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  await user.click(await screen.findByRole("option", { name: /first/ }))
  await user.click(screen.getByRole("option", { name: /Test MCP/ }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(values()).toMatchObject({
    actions: [action.action],
    mcp_integrations: [integration.id],
  })
  await user.click(screen.getByRole("button", { name: /Alpha tools.alpha/ }))
  const approval = screen.getByRole("button", {
    name: `Require approval for ${action.action}`,
  })
  await user.click(approval)
  expect(values().tool_approvals).toEqual({ [action.action]: true })
  await user.click(approval)
  // Read the form directly: JSON.stringify would hide an undefined value.
  expect(currentForm.getValues("inputs.tool_approvals")).toBeUndefined()
  expect(currentForm.getValues("inputs.mcp_integrations")).toEqual([
    integration.id,
  ])
  await user.click(screen.getByRole("button", { name: "Remove Test MCP" }))
  expect(currentForm.getValues("inputs.mcp_integrations")).toBeUndefined()
  expect(currentForm.getValues("inputs.actions")).toEqual([action.action])
})

it("keeps registry tools editable without integration read access", async () => {
  jest.mocked(useScopeCheck).mockReturnValue(false)
  // The request is skipped; a stale 403 from another surface must not block.
  jest.mocked(useListMcpIntegrations).mockReturnValue({
    mcpIntegrations: undefined,
    mcpIntegrationsIsLoading: false,
    mcpIntegrationsError: new Error("Forbidden") as never,
  })
  const user = userEvent.setup()
  render(<TestField inputs={{ mcp_integrations: [integration.id] }} />)
  expect(useListMcpIntegrations).toHaveBeenLastCalledWith(
    "workspace-test",
    undefined,
    { enabled: false }
  )
  expect(
    screen.queryByText("Tools could not be loaded.")
  ).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  await user.click(await screen.findByRole("option", { name: /first/ }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(values()).toEqual({
    actions: [action.action],
    mcp_integrations: [integration.id],
  })
})

it("waits for scopes before requesting MCP integrations", () => {
  jest.mocked(useScopeCheck).mockReturnValue(undefined)
  render(<TestField />)
  expect(useListMcpIntegrations).toHaveBeenLastCalledWith(
    "workspace-test",
    undefined,
    { enabled: false }
  )
  expect(screen.getByText("Loading tools...")).toBeInTheDocument()
})

it("limits preset-agent schemas to registry actions", async () => {
  const user = userEvent.setup()
  render(<TestField properties={presetProperties} />)
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  expect(screen.queryByText("MCP servers")).not.toBeInTheDocument()
  await user.click(await screen.findByRole("option", { name: /first/ }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(values()).toEqual({ actions: [action.action] })
  await user.click(screen.getByRole("button", { name: /Alpha tools.alpha/ }))
  expect(screen.queryByText("Ask")).not.toBeInTheDocument()
  expect(screen.queryByText("Auto")).not.toBeInTheDocument()
})

it("renders an actions expression without coercing it", () => {
  const expression = "${{ ACTIONS.result }}"
  render(<TestField inputs={{ actions: expression }} />)
  expect(screen.getByRole("textbox", { name: "Expression" })).toHaveValue(
    expression
  )
  expect(
    screen.queryByRole("button", { name: "Add tools" })
  ).not.toBeInTheDocument()
  expect(values()).toEqual({ actions: expression })
})

it("does not fold or overwrite an MCP expression after picker changes", async () => {
  const expression = "${{ MCP.result }}"
  expect(
    getAgentToolsFolding(agentProperties, { mcp_integrations: expression })
      .mcpField
  ).toBeNull()
  const user = userEvent.setup()
  render(<TestField inputs={{ mcp_integrations: expression }} />)
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  await user.click(await screen.findByRole("option", { name: /first/ }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(values()).toEqual({
    actions: [action.action],
    mcp_integrations: expression,
  })
})

it("shows and preserves an unknown saved action on no-op Done", async () => {
  const user = userEvent.setup()
  render(<TestField inputs={{ actions: ["unknown.saved"] }} />)
  expect(screen.getByText("Unavailable")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(values()).toEqual({ actions: ["unknown.saved"] })
  expect(screen.getByTestId("dirty")).toHaveTextContent("false")
})

it("does not offer an action agents cannot call", async () => {
  jest.mocked(useBuilderRegistryActions).mockReturnValue({
    registryActions: [action, registryTool("core.script.run_python")],
    registryActionsIsLoading: false,
    registryActionsError: null,
    getRegistryAction: jest.fn(),
  })
  const user = userEvent.setup()
  render(<TestField inputs={{ actions: ["core.script.run_python"] }} />)
  expect(screen.getByText("Unavailable")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  expect(await screen.findByRole("option", { name: /first/ })).toBeVisible()
  expect(
    screen.queryByRole("option", { name: /run python/ })
  ).not.toBeInTheDocument()
})

it("mounts fewer than 40 action options for a 1,500-entry catalog", async () => {
  jest.mocked(useBuilderRegistryActions).mockReturnValue({
    registryActions: largeToolCatalog(),
    registryActionsIsLoading: false,
    registryActionsError: null,
    getRegistryAction: jest.fn(),
  })
  const user = userEvent.setup()
  render(<TestField properties={presetProperties} />)
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  const options = await screen.findAllByRole("option")
  expect(options.length).toBeGreaterThan(0)
  expect(options.length).toBeLessThan(40)
})

it("keeps the expression editor while an expression is edited into plain text", async () => {
  const user = userEvent.setup()
  render(<TestField inputs={{ actions: "${{ ACTIONS.result }}" }} />)
  await user.type(
    screen.getByRole("textbox", { name: "Expression" }),
    "{Backspace}"
  )
  expect(screen.getByRole("textbox", { name: "Expression" })).toHaveValue(
    "${{ ACTIONS.result }"
  )
  expect(
    screen.queryByRole("button", { name: "Add tools" })
  ).not.toBeInTheDocument()
  expect(currentForm.getValues("inputs.actions")).toBe("${{ ACTIONS.result }")
})

it("renders the expression editor for a non-expression string", () => {
  render(<TestField inputs={{ actions: "tools.alpha.first" }} />)
  expect(screen.getByRole("textbox", { name: "Expression" })).toHaveValue(
    "tools.alpha.first"
  )
})

it("treats null args like unset ones and writes nothing until a change", async () => {
  const nulls = { actions: null, mcp_integrations: null, tool_approvals: null }
  const user = userEvent.setup()
  render(<TestField inputs={nulls} />)
  expect(screen.getByText("No tools selected.")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  expect(screen.getByText("MCP servers")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(currentForm.getValues("inputs")).toEqual(nulls)
  expect(screen.getByTestId("dirty")).toHaveTextContent("false")

  await user.click(screen.getByRole("button", { name: "Add tools" }))
  await user.click(await screen.findByRole("option", { name: /first/ }))
  await user.click(screen.getByRole("option", { name: /Test MCP/ }))
  await user.click(screen.getByRole("button", { name: "Done" }))
  expect(currentForm.getValues("inputs")).toEqual({
    actions: [action.action],
    mcp_integrations: [integration.id],
    tool_approvals: null,
  })
  await user.click(screen.getByRole("button", { name: /Alpha tools.alpha/ }))
  await user.click(
    screen.getByRole("button", {
      name: `Require approval for ${action.action}`,
    })
  )
  expect(currentForm.getValues("inputs.tool_approvals")).toEqual({
    [action.action]: true,
  })
})

it("shows the count without repeating the Tools title of the form label", () => {
  render(<TestField inputs={{ actions: [action.action] }} />)
  expect(screen.queryByRole("heading", { name: "Tools" })).toBeNull()
  expect(screen.getByText("1 selected")).toBeInTheDocument()
  expect(
    screen.getByRole("button", { name: "Search allowed tools" })
  ).toBeInTheDocument()
})

it("counts a selected MCP server in the header", () => {
  render(
    <TestField
      inputs={{ actions: [action.action], mcp_integrations: [integration.id] }}
    />
  )
  expect(screen.getByText("2 selected")).toBeInTheDocument()
})
