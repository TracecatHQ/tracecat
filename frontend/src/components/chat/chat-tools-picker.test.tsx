import { render, screen } from "@testing-library/react"
import type { MCPIntegrationRead } from "@/client"
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
  stdio_command: null,
  stdio_args: null,
  has_stdio_env: false,
  timeout: null,
  created_at: "2024-01-01T00:00:00.000Z",
  updated_at: "2024-01-01T00:00:00.000Z",
} satisfies MCPIntegrationRead

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
})
