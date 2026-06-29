import { fireEvent, render, screen } from "@testing-library/react"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import { ChatToolsPicker } from "@/components/chat/chat-tools-picker"

jest.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  PopoverContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  PopoverTrigger: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}))

const runrevealIntegration = {
  id: "mcp-1",
  workspace_id: "workspace-1",
  name: "RunReveal",
  description: "RunReveal MCP",
  slug: "runreveal",
  server_type: "http",
  server_uri: "https://mcp.example.test",
  auth_type: "OAUTH2",
  oauth_integration_id: null,
  state: "connected",
  stdio_command: null,
  stdio_args: null,
  has_stdio_env: false,
  timeout: null,
  created_at: "2024-01-01T00:00:00.000Z",
  updated_at: "2024-01-01T00:00:00.000Z",
} satisfies MCPIntegrationRead

function registryAction(
  action: string,
  overrides: Partial<RegistryActionReadMinimal> = {}
): RegistryActionReadMinimal {
  return {
    id: action,
    name: action,
    description: `${action} description`,
    namespace: action.split(".").slice(0, -1).join("."),
    type: "udf",
    origin: "tracecat",
    action,
    ...overrides,
  }
}

describe("ChatToolsPicker", () => {
  it("hides MCP integrations when they are not enabled for the chat surface", () => {
    render(
      <ChatToolsPicker
        registryActions={[]}
        selectedTools={[]}
        onToolsChange={jest.fn()}
        mcpIntegrations={[runrevealIntegration]}
        selectedMcpIntegrations={[runrevealIntegration.id]}
        onMcpChange={jest.fn()}
        mcpEnabled={false}
      />
    )

    expect(screen.getByRole("button", { name: "Tools" })).toBeInTheDocument()
    expect(screen.queryByText("MCP integrations")).not.toBeInTheDocument()
    expect(screen.queryByText("RunReveal")).not.toBeInTheDocument()
  })

  it("shows MCP integrations when they are enabled", () => {
    render(
      <ChatToolsPicker
        registryActions={[]}
        selectedTools={[]}
        onToolsChange={jest.fn()}
        mcpIntegrations={[runrevealIntegration]}
        selectedMcpIntegrations={[runrevealIntegration.id]}
        onMcpChange={jest.fn()}
        mcpEnabled
      />
    )

    expect(
      screen.getByRole("button", { name: "Tools (1)" })
    ).toBeInTheDocument()
    expect(screen.getByText("MCP integrations")).toBeInTheDocument()
    expect(screen.getByText("RunReveal")).toBeInTheDocument()
  })

  it("shows stale selected MCP integrations so they can be removed", () => {
    const onMcpChange = jest.fn()

    render(
      <ChatToolsPicker
        registryActions={[]}
        selectedTools={[]}
        onToolsChange={jest.fn()}
        mcpIntegrations={[]}
        selectedMcpIntegrations={["missing-mcp"]}
        onMcpChange={onMcpChange}
        mcpEnabled
      />
    )

    expect(
      screen.getByRole("button", { name: "Tools (1)" })
    ).toBeInTheDocument()
    expect(screen.getByText("missing-mcp")).toBeInTheDocument()
    expect(
      screen.getByText("This MCP integration is no longer available.")
    ).toBeInTheDocument()

    fireEvent.click(screen.getByText("missing-mcp"))

    expect(onMcpChange).toHaveBeenCalledWith([])
  })

  it("shows stale selected registry tools so they can be removed", () => {
    const onToolsChange = jest.fn()

    render(
      <ChatToolsPicker
        registryActions={[]}
        selectedTools={["tools.deleted.action"]}
        onToolsChange={onToolsChange}
        mcpIntegrations={[]}
        selectedMcpIntegrations={[]}
        onMcpChange={jest.fn()}
      />
    )

    expect(
      screen.getByRole("button", { name: "Tools (1)" })
    ).toBeInTheDocument()
    expect(screen.getByText("tools.deleted.action")).toBeInTheDocument()
    expect(screen.getByText("No longer available")).toBeInTheDocument()

    fireEvent.click(screen.getByText("tools.deleted.action"))

    expect(onToolsChange).toHaveBeenCalledWith([])
  })

  it("allows regular chat surfaces to add workspace-chat default tools", () => {
    const onToolsChange = jest.fn()

    render(
      <ChatToolsPicker
        registryActions={[
          registryAction("core.table.search_rows", {
            default_title: "Search rows",
            display_group: "Tables",
          }),
        ]}
        selectedTools={[]}
        onToolsChange={onToolsChange}
        mcpIntegrations={[]}
        selectedMcpIntegrations={[]}
        onMcpChange={jest.fn()}
        surface="regular"
      />
    )

    fireEvent.change(
      screen.getByPlaceholderText("Search capabilities & tools..."),
      {
        target: { value: "search rows" },
      }
    )

    expect(screen.getByText("Search rows")).toBeInTheDocument()
    expect(screen.queryByText("Included by default")).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("Search rows"))

    expect(onToolsChange).toHaveBeenCalledWith(["core.table.search_rows"])
  })

  it("shows selected default registry tools in search so they can be removed", () => {
    const onToolsChange = jest.fn()

    render(
      <ChatToolsPicker
        registryActions={[
          registryAction("core.cases.list_cases", {
            default_title: "List cases",
            display_group: "Cases",
          }),
        ]}
        selectedTools={["core.cases.list_cases"]}
        onToolsChange={onToolsChange}
        mcpIntegrations={[]}
        selectedMcpIntegrations={[]}
        onMcpChange={jest.fn()}
        surface="workspace-chat"
      />
    )

    expect(
      screen.getByRole("button", { name: "Tools (1)" })
    ).toBeInTheDocument()

    fireEvent.change(
      screen.getByPlaceholderText("Search capabilities & tools..."),
      {
        target: { value: "list cases" },
      }
    )

    expect(screen.getByText("List cases")).toBeInTheDocument()
    expect(screen.getByText("Included by default")).toBeInTheDocument()

    fireEvent.click(screen.getByText("List cases"))

    expect(onToolsChange).toHaveBeenCalledWith([])
  })

  it("hides add-on capabilities when agent add-ons are disabled", () => {
    render(
      <ChatToolsPicker
        registryActions={[]}
        selectedTools={[]}
        onToolsChange={jest.fn()}
        mcpIntegrations={[]}
        selectedMcpIntegrations={[]}
        onMcpChange={jest.fn()}
        agentAddonsEnabled={false}
        surface="workspace-chat"
      />
    )

    expect(screen.getByText("Cases")).toBeInTheDocument()
    expect(screen.queryByText("Agent presets")).not.toBeInTheDocument()
  })
})
