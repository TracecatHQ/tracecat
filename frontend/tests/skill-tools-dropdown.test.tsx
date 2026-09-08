import { fireEvent, render, screen } from "@testing-library/react"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import { SkillToolsDropdown } from "@/components/skills/skill-tools-dropdown"
import { readSkillFrontmatterTools } from "@/lib/skill-tools"

const mockRegistryAction: RegistryActionReadMinimal = {
  id: "action-1",
  name: "get_case",
  description: "Get one case.",
  namespace: "core.cases",
  type: "udf",
  origin: "tracecat_registry.core.cases",
  default_title: "Get case",
  display_group: "Cases",
  action: "core.cases.get_case",
}

let mockMcpIntegrations: MCPIntegrationRead[] = []

jest.mock("@/lib/hooks", () => ({
  useRegistryActions: () => ({
    registryActions: [mockRegistryAction],
    registryActionsIsLoading: false,
    registryActionsError: null,
  }),
  useListMcpIntegrations: () => ({
    mcpIntegrations: mockMcpIntegrations,
    mcpIntegrationsIsLoading: false,
    mcpIntegrationsError: null,
  }),
}))

describe("SkillToolsDropdown", () => {
  beforeAll(() => {
    global.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  })

  it("labels the input and writes a selected tool to frontmatter", () => {
    const handleChange = jest.fn()
    render(
      <SkillToolsDropdown
        workspaceId="workspace-1"
        frontmatter="name: incident-triage"
        onChange={handleChange}
      />
    )

    const input = screen.getByRole("textbox", { name: "Tools" })
    fireEvent.focus(input)
    fireEvent.mouseDown(screen.getByText("Get case"))
    fireEvent.click(screen.getByText("Get case"))

    expect(handleChange).toHaveBeenCalledTimes(1)
    expect(readSkillFrontmatterTools(handleChange.mock.calls[0][0])).toEqual({
      valid: true,
      tools: ["core.cases.get_case"],
    })
  })

  it("disables structured editing when metadata.tools is malformed", () => {
    render(
      <SkillToolsDropdown
        workspaceId="workspace-1"
        frontmatter={`name: incident-triage
metadata:
  tools: core.cases.get_case`}
        onChange={jest.fn()}
      />
    )

    expect(screen.getByRole("textbox", { name: "Tools" })).toBeDisabled()
    expect(
      screen.getByText("metadata.tools must be a YAML list.")
    ).toBeInTheDocument()
  })
})

it("shows malformed IDs and lets users remove them", () => {
  const onChange = jest.fn()
  render(
    <SkillToolsDropdown
      workspaceId="workspace-1"
      frontmatter='metadata: { tools: ["not-a-tool", "core.cases.get_case"] }'
      onChange={onChange}
    />
  )
  expect(screen.getByText(/Invalid tool IDs: not-a-tool/)).toBeInTheDocument()
  fireEvent.focus(screen.getByRole("textbox", { name: "Tools" }))
  expect(screen.queryByRole("option")).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "Remove not-a-tool" }))
  expect(readSkillFrontmatterTools(onChange.mock.calls[0][0])).toEqual({
    valid: true,
    tools: ["core.cases.get_case"],
  })
})

it("warns about individual stdio grants and allows their removal", () => {
  mockMcpIntegrations = [
    {
      id: "integration-1",
      workspace_id: "workspace-1",
      name: "Synthetic",
      slug: "synthetic",
      server_type: "stdio",
      description: null,
      server_uri: null,
      oauth_integration_id: null,
      stdio_command: "synthetic",
      stdio_args: [],
      timeout: 30,
      auth_type: "NONE",
      state: "connected",
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
    },
  ]
  try {
    const onChange = jest.fn()
    render(
      <SkillToolsDropdown
        workspaceId="workspace-1"
        frontmatter="metadata: {tools: [mcp.synthetic.read, mcp.synthetic]}"
        onChange={onChange}
      />
    )
    expect(
      screen.getByText(/Invalid tool IDs: mcp.synthetic.read/)
    ).toBeInTheDocument()
    fireEvent.focus(screen.getByRole("textbox", { name: "Tools" }))
    expect(screen.queryByRole("option")).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Remove mcp.synthetic.read" })
    )
    expect(
      readSkillFrontmatterTools(onChange.mock.calls[0][0], mockMcpIntegrations)
    ).toEqual({
      valid: true,
      tools: ["mcp.synthetic"],
    })
  } finally {
    mockMcpIntegrations = []
  }
})
