import { fireEvent, render, screen } from "@testing-library/react"
import type { RegistryActionReadMinimal } from "@/client"
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

jest.mock("@/lib/hooks", () => ({
  useRegistryActions: () => ({
    registryActions: [mockRegistryAction],
    registryActionsIsLoading: false,
    registryActionsError: null,
  }),
  useListMcpIntegrations: () => ({
    mcpIntegrations: [],
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
