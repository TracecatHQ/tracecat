import { render, screen } from "@testing-library/react"
import type { WorkspaceRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { WorkspaceRuntimeSettings } from "@/components/settings/workspace-runtime-settings"

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))
jest.mock("@/lib/hooks", () => ({
  useWorkspaceSettings: () => ({
    updateWorkspace: jest.fn(),
    isUpdating: false,
  }),
}))

const workspace: WorkspaceRead = {
  id: "workspace-example",
  organization_id: "organization-example",
  name: "Example",
  settings: {
    error_masking_mode: null,
    effective_allowed_attachment_extensions: [],
    effective_allowed_attachment_mime_types: [],
  },
  effective_error_masking_mode: "conservative",
}

describe("workspace error masking controls", () => {
  it("shows inheritance and the effective organization mode", () => {
    jest.mocked(useScopeCheck).mockReturnValue(true)
    render(<WorkspaceRuntimeSettings workspace={workspace} />)
    expect(
      screen.getByRole("combobox", { name: "Error masking" })
    ).toHaveTextContent("Inherit organization")
    expect(
      screen.getByText(/Saved effective mode: conservative/)
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled()
  })

  it("disables changes without workspace update permission", () => {
    jest.mocked(useScopeCheck).mockReturnValue(false)
    render(<WorkspaceRuntimeSettings workspace={workspace} />)
    expect(
      screen.getByRole("combobox", { name: "Error masking" })
    ).toBeDisabled()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })
})
