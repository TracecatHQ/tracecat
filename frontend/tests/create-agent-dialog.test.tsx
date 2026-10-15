import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useRouter } from "next/navigation"
import type { ReactNode } from "react"
import type { ApiError } from "@/client/core/ApiError"
import { CreateAgentDialog } from "@/components/agents/create-agent-dialog"
import {
  useCreateAgentPreset,
  useMoveAgentPreset,
} from "@/hooks/use-agent-presets"
import { useUserScopes, useWorkspaceAgentModels } from "@/lib/hooks"

const mockCreateAgentPreset = jest.fn()
const mockMoveAgentPreset = jest.fn()
const mockOnOpenChange = jest.fn()
const mockRouterPush = jest.fn()
const mockRouterReplace = jest.fn()
const mockSetSettingsOpen = jest.fn()
const mockSetActiveSection = jest.fn()
const mockHasEntitlement = jest.fn<boolean, [string]>(() => false)

jest.mock("next/navigation", () => ({
  useRouter: jest.fn(),
}))

jest.mock("@/components/settings/settings-modal-context", () => ({
  useSettingsModal: () => ({
    setOpen: mockSetSettingsOpen,
    setActiveSection: mockSetActiveSection,
  }),
}))

jest.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div>{children}</div> : null,
  DialogBody: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
  DialogFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

jest.mock("@/hooks/use-agent-presets", () => ({
  useCreateAgentPreset: jest.fn(),
  useMoveAgentPreset: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  useWorkspaceAgentModels: jest.fn(),
  useUserScopes: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => mockHasEntitlement(key),
    isLoading: false,
    hasEntitlementData: true,
  }),
}))

const catalogModels = [
  {
    id: "catalog-fallback",
    custom_provider_id: "provider-custom",
    organization_id: null,
    model_provider: "custom",
    model_name: "custom-fast",
    model_metadata: {},
    custom_provider_name: "Custom",
    custom_provider_base_url: "https://models.example.com/v1",
  },
  {
    id: "catalog-default",
    custom_provider_id: null,
    organization_id: null,
    model_provider: "openai",
    model_name: "gpt-5.5",
    model_metadata: {},
  },
]

const modelReadError = new Error("request failed") as ApiError

type SetupMocksOptions = {
  orgScopes?: string[]
  workspaceScopes?: string[]
  models?: typeof catalogModels
  defaultCatalogId?: string | null
  hasOrgDefault?: boolean
  modelsLoading?: boolean
  modelsError?: ApiError | null
}

function setupMocks({
  orgScopes = ["org:update"],
  workspaceScopes = [],
  models = catalogModels,
  defaultCatalogId = null,
  hasOrgDefault = defaultCatalogId !== null,
  modelsLoading = false,
  modelsError = null,
}: SetupMocksOptions = {}) {
  jest.mocked(useUserScopes).mockImplementation((workspaceId) => ({
    userScopes: { scopes: workspaceId ? workspaceScopes : orgScopes },
    isLoading: false,
    error: null,
  }))
  jest.mocked(useRouter).mockReturnValue({
    back: jest.fn(),
    forward: jest.fn(),
    prefetch: jest.fn(),
    push: mockRouterPush,
    refresh: jest.fn(),
    replace: mockRouterReplace,
  })
  jest.mocked(useWorkspaceAgentModels).mockReturnValue({
    models: modelsLoading || modelsError ? undefined : models,
    defaultCatalogId,
    hasOrgDefault,
    workspaceSubsetDisabled: false,
    modelsLoading,
    modelsError,
  })
  jest.mocked(useCreateAgentPreset).mockReturnValue({
    createAgentPreset: mockCreateAgentPreset,
    createAgentPresetIsPending: false,
    createAgentPresetError: null,
  })
  jest.mocked(useMoveAgentPreset).mockReturnValue({
    moveAgentPreset: mockMoveAgentPreset,
    moveAgentPresetIsPending: false,
    moveAgentPresetError: null,
  })
}

function renderCreateAgentDialog(currentPath?: string | null) {
  render(
    <CreateAgentDialog
      open={true}
      onOpenChange={mockOnOpenChange}
      currentPath={currentPath}
    />
  )
}

describe("CreateAgentDialog", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockHasEntitlement.mockReturnValue(false)
    mockCreateAgentPreset.mockResolvedValue({
      id: "preset-1",
      name: "QA agent",
    })
    mockMoveAgentPreset.mockResolvedValue(undefined)
  })

  it("requires a configured default model even when workspace models are available", async () => {
    const user = userEvent.setup()
    setupMocks()
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Set up model provider" })
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        "Choose a default model in organization settings before creating an agent."
      )
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Create agent" })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "Configure models" })
    ).toHaveAttribute("href", "/organization/settings/agent")

    await user.click(screen.getByRole("link", { name: "Configure models" }))
    expect(mockOnOpenChange).toHaveBeenCalledWith(false)
  })

  it("directs non-admins to an organization administrator when no default is configured", () => {
    setupMocks({ orgScopes: [] })
    renderCreateAgentDialog()

    expect(
      screen.getByText(
        "Ask an organization administrator to configure a default model before creating an agent."
      )
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Configure models" })
    ).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
  })

  it("hides the configuration link while organization scopes are loading", () => {
    setupMocks()
    jest.mocked(useUserScopes).mockReturnValue({
      userScopes: undefined,
      isLoading: true,
      error: null,
    })
    renderCreateAgentDialog()

    expect(
      screen.queryByRole("link", { name: "Configure models" })
    ).not.toBeInTheDocument()
  })

  it("uses the configured default model when it is enabled for the workspace", async () => {
    const user = userEvent.setup()
    setupMocks({ defaultCatalogId: "catalog-default" })
    renderCreateAgentDialog()

    await user.type(screen.getByLabelText("Name"), "Default model agent")
    await user.click(screen.getByRole("button", { name: "Create agent" }))

    await waitFor(() => {
      expect(mockCreateAgentPreset).toHaveBeenCalledWith({
        name: "Default model agent",
        model_provider: "openai",
        model_name: "gpt-5.5",
        catalog_id: "catalog-default",
        base_url: undefined,
        description: undefined,
      })
    })
  })

  it("starts an agent from library skills and numbers a taken name", async () => {
    const user = userEvent.setup()
    setupMocks({ defaultCatalogId: "catalog-default" })
    render(
      <CreateAgentDialog
        open={true}
        onOpenChange={mockOnOpenChange}
        librarySkills={["alert-triage"]}
        defaultName="Alert Triage 2"
        takenSlugs={["alert-triage", "alert-triage-2", "triage-bot"]}
      />
    )

    const name = screen.getByLabelText("Name")
    expect(name).toHaveValue("Alert Triage 2")
    expect(
      screen.getByText(/starts with alert-triage attached/)
    ).toBeInTheDocument()
    await user.clear(name)
    await user.type(name, "Triage Bot")
    await user.click(screen.getByRole("button", { name: "Create agent" }))

    await waitFor(() => {
      expect(mockCreateAgentPreset).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Triage Bot 2",
          library_skills: ["alert-triage"],
        })
      )
    })
  })

  it("does not move the preset into a folder without agent add-ons", async () => {
    const user = userEvent.setup()
    setupMocks({ defaultCatalogId: "catalog-fallback" })
    renderCreateAgentDialog("/legacy/")

    await user.type(screen.getByLabelText("Name"), "OSS agent")
    await user.click(screen.getByRole("button", { name: "Create agent" }))

    await waitFor(() => {
      expect(mockCreateAgentPreset).toHaveBeenCalled()
      expect(mockRouterPush).toHaveBeenCalledWith(
        "/workspaces/workspace-1/agents/preset-1"
      )
    })
    expect(mockMoveAgentPreset).not.toHaveBeenCalled()
  })

  it("moves the preset into the current folder with agent add-ons", async () => {
    const user = userEvent.setup()
    mockHasEntitlement.mockImplementation((key) => key === "agent_addons")
    setupMocks({ defaultCatalogId: "catalog-fallback" })
    renderCreateAgentDialog("/legacy/")

    await user.type(screen.getByLabelText("Name"), "Enterprise agent")
    await user.click(screen.getByRole("button", { name: "Create agent" }))

    await waitFor(() => {
      expect(mockMoveAgentPreset).toHaveBeenCalledWith({
        presetId: "preset-1",
        folder_path: "/legacy/",
      })
    })
  })

  it("shows setup guidance when no workspace models are enabled", () => {
    setupMocks({ models: [] })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Set up model provider" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Create agent" })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "Configure models" })
    ).toBeInTheDocument()
    expect(mockCreateAgentPreset).not.toHaveBeenCalled()
  })

  it("asks to enable the default model when the workspace cannot use it", () => {
    setupMocks({ hasOrgDefault: true })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Enable the default model" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
  })

  it("opens workspace model settings for a workspace admin when the default is excluded", async () => {
    const user = userEvent.setup()
    setupMocks({
      orgScopes: [],
      workspaceScopes: ["workspace:update"],
      hasOrgDefault: true,
      models: catalogModels.filter((model) => model.id !== "catalog-default"),
    })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Enable the default model" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Configure models" })
    ).not.toBeInTheDocument()

    await user.click(
      screen.getByRole("button", { name: "Configure workspace models" })
    )

    expect(mockOnOpenChange).toHaveBeenCalledWith(false)
    expect(mockSetActiveSection).toHaveBeenCalledWith("workspace-models")
    expect(mockSetSettingsOpen).toHaveBeenCalledWith(true)
    expect(mockCreateAgentPreset).not.toHaveBeenCalled()
  })

  it("directs non-managers to a workspace administrator when the default is excluded", () => {
    setupMocks({
      orgScopes: [],
      workspaceScopes: ["agent:create"],
      hasOrgDefault: true,
      models: [],
    })
    renderCreateAgentDialog()

    expect(
      screen.getByText(
        "The organization default model is not enabled for this workspace. Ask a workspace administrator to enable it before creating an agent."
      )
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Configure workspace models" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Configure models" })
    ).not.toBeInTheDocument()
  })

  it("keeps organization setup guidance for workspace admins when no default exists", () => {
    setupMocks({ orgScopes: [], workspaceScopes: ["workspace:update"] })
    renderCreateAgentDialog()

    expect(
      screen.getByText(
        "Ask an organization administrator to configure a default model before creating an agent."
      )
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Configure workspace models" })
    ).not.toBeInTheDocument()
  })

  it("hides the workspace settings action until workspace permissions load", () => {
    setupMocks({ hasOrgDefault: true, models: [] })
    jest.mocked(useUserScopes).mockImplementation((workspaceId) => ({
      userScopes: workspaceId ? undefined : { scopes: ["org:update"] },
      isLoading: Boolean(workspaceId),
      error: null,
    }))
    renderCreateAgentDialog()

    expect(
      screen.queryByRole("button", { name: "Configure workspace models" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Configure models" })
    ).not.toBeInTheDocument()
  })

  it("shows a loading state without the create form while model queries load", () => {
    setupMocks({ modelsLoading: true })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Loading models" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Configure models" })
    ).not.toBeInTheDocument()
  })

  it("shows a neutral error state without setup guidance when a model query fails", () => {
    setupMocks({ modelsError: modelReadError })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Unable to load models" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("heading", { name: "Set up model provider" })
    ).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Configure models" })
    ).not.toBeInTheDocument()
  })

  it("does not query models while the dialog is closed", () => {
    setupMocks()
    render(<CreateAgentDialog open={false} onOpenChange={mockOnOpenChange} />)

    expect(useWorkspaceAgentModels).not.toHaveBeenCalled()
    expect(useUserScopes).not.toHaveBeenCalled()
  })

  it("uses the configured custom-provider default model and preserves its base URL", async () => {
    const user = userEvent.setup()
    setupMocks({ defaultCatalogId: "catalog-fallback" })
    renderCreateAgentDialog()

    await user.type(screen.getByLabelText("Name"), "Custom agent")
    await user.click(screen.getByRole("button", { name: "Create agent" }))

    await waitFor(() => {
      expect(mockCreateAgentPreset).toHaveBeenCalledWith({
        name: "Custom agent",
        model_provider: "custom",
        model_name: "custom-fast",
        catalog_id: "catalog-fallback",
        base_url: "https://models.example.com/v1",
        description: undefined,
      })
    })
  })
})
