import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useRouter } from "next/navigation"
import type { ReactNode } from "react"
import type { DefaultModelSelection } from "@/client"
import type { ApiError } from "@/client/core/ApiError"
import { CreateAgentDialog } from "@/components/agents/create-agent-dialog"
import {
  useCreateAgentPreset,
  useMoveAgentPreset,
} from "@/hooks/use-agent-presets"
import {
  useAgentDefaultModel,
  useUserScopes,
  useWorkspaceAgentModels,
} from "@/lib/hooks"

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
  useAgentDefaultModel: jest.fn(),
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

const customProviders = [
  {
    id: "provider-custom",
    organization_id: "org-1",
    display_name: "Custom",
    base_url: "https://models.example.com/v1",
    passthrough: true,
    api_key_header: "Authorization",
    last_refreshed_at: null,
  },
]

const modelReadError = new Error("request failed") as ApiError

const builtInDefaultSelection: DefaultModelSelection = {
  catalog_id: "catalog-default",
  model_name: "gpt-5.5",
  model_provider: "openai",
  custom_provider_id: null,
}

const customDefaultSelection: DefaultModelSelection = {
  catalog_id: "catalog-fallback",
  model_name: "custom-fast",
  model_provider: "custom",
  custom_provider_id: "provider-custom",
}

type SetupMocksOptions = {
  orgScopes?: string[]
  workspaceScopes?: string[]
  defaultModel?: string | null
  defaultModelSelection?: DefaultModelSelection | null
  defaultModelLoading?: boolean
  defaultModelError?: Error | null
  defaultModelSelectionLoading?: boolean
  defaultModelSelectionError?: Error | null
  models?: typeof catalogModels
  modelsLoading?: boolean
  modelsError?: ApiError | null
  providersLoading?: boolean
  providersError?: ApiError | null
}

function setupMocks({
  orgScopes = ["org:update"],
  workspaceScopes = [],
  defaultModel = null,
  defaultModelSelection = null,
  models = catalogModels,
  defaultModelLoading = false,
  defaultModelError = null,
  defaultModelSelectionLoading = false,
  defaultModelSelectionError = null,
  modelsLoading = false,
  modelsError = null,
  providersLoading = false,
  providersError = null,
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
    models,
    providers: providersLoading || providersError ? undefined : customProviders,
    catalogLoading: modelsLoading,
    catalogError: modelsError,
    providersLoading,
    providersError,
    modelsLoading: modelsLoading || providersLoading,
    modelsError: modelsError ?? providersError,
  })
  jest.mocked(useAgentDefaultModel).mockReturnValue({
    defaultModel,
    defaultModelSelection,
    defaultModelSelectionLoading,
    defaultModelSelectionError,
    defaultModelLoading: defaultModelLoading || defaultModelSelectionLoading,
    defaultModelError: defaultModelError ?? defaultModelSelectionError,
    updateDefaultModel: jest.fn(),
    isUpdating: false,
    updateError: null,
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
    setupMocks({
      defaultModelSelection: {
        catalog_id: "catalog-default",
        model_name: "gpt-5.5",
        model_provider: "openai",
        custom_provider_id: null,
      },
    })
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

  it("does not move the preset into a folder without agent add-ons", async () => {
    const user = userEvent.setup()
    setupMocks({ defaultModelSelection: customDefaultSelection })
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
    setupMocks({ defaultModelSelection: customDefaultSelection })
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

  it("does not fall back to a legacy model name when the selected catalog model is unavailable", () => {
    setupMocks({
      defaultModel: "gpt-5.5",
      defaultModelSelection: {
        catalog_id: "catalog-missing",
        model_name: "gpt-5.5",
        model_provider: "openai",
        custom_provider_id: null,
      },
    })
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
      defaultModelSelection: {
        catalog_id: "catalog-default",
        model_name: "gpt-5.5",
        model_provider: "openai",
        custom_provider_id: null,
      },
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
      defaultModelSelection: {
        catalog_id: "catalog-default",
        model_name: "gpt-5.5",
        model_provider: "openai",
        custom_provider_id: null,
      },
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
    setupMocks({
      defaultModelSelection: {
        catalog_id: "catalog-default",
        model_name: "gpt-5.5",
        model_provider: "openai",
        custom_provider_id: null,
      },
      models: [],
    })
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

  it("keeps organization setup guidance when only an unresolved legacy name remains", () => {
    setupMocks({
      defaultModel: "unavailable-model",
      workspaceScopes: ["workspace:update"],
    })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Set up model provider" })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "Configure models" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Configure workspace models" })
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

  it("shows a loading state without the create form while the default query loads", () => {
    setupMocks({ defaultModelSelectionLoading: true })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Loading models" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
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

  it.each([{ providersError: modelReadError }, { providersLoading: true }])(
    "creates a built-in agent despite unavailable provider data: %o",
    async (providerState) => {
      const user = userEvent.setup()
      setupMocks({
        defaultModelSelection: builtInDefaultSelection,
        ...providerState,
      })
      renderCreateAgentDialog()

      await user.type(screen.getByLabelText("Name"), "Built-in agent")
      await user.click(screen.getByRole("button", { name: "Create agent" }))

      await waitFor(() => {
        expect(mockCreateAgentPreset).toHaveBeenCalledWith({
          name: "Built-in agent",
          model_provider: "openai",
          model_name: "gpt-5.5",
          catalog_id: "catalog-default",
          base_url: undefined,
          description: undefined,
        })
      })
    }
  )

  it("blocks custom-model creation when provider data fails", () => {
    setupMocks({
      defaultModelSelection: customDefaultSelection,
      providersError: modelReadError,
    })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Unable to load models" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
    expect(mockCreateAgentPreset).not.toHaveBeenCalled()
  })

  it("waits for provider data before allowing custom-model creation", () => {
    setupMocks({
      defaultModelSelection: customDefaultSelection,
      providersLoading: true,
    })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Loading models" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
    expect(mockCreateAgentPreset).not.toHaveBeenCalled()
  })

  it("shows a neutral error state when the default model query fails", () => {
    setupMocks({ defaultModelSelectionError: new Error("request failed") })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Unable to load models" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
  })

  it.each([
    ["fails", { defaultModelError: new Error("request failed") }],
    ["is loading", { defaultModelLoading: true }],
  ] as const)(
    "creates with the canonical default when the legacy query %s",
    async (_label, legacyState) => {
      const user = userEvent.setup()
      setupMocks({
        defaultModelSelection: {
          catalog_id: "catalog-default",
          model_name: "gpt-5.5",
          model_provider: "openai",
          custom_provider_id: null,
        },
        ...legacyState,
      })
      renderCreateAgentDialog()

      await user.type(screen.getByLabelText("Name"), "Canonical default agent")
      await user.click(screen.getByRole("button", { name: "Create agent" }))

      await waitFor(() => {
        expect(mockCreateAgentPreset).toHaveBeenCalledWith({
          name: "Canonical default agent",
          model_provider: "openai",
          model_name: "gpt-5.5",
          catalog_id: "catalog-default",
          base_url: undefined,
          description: undefined,
        })
      })
    }
  )

  it("blocks creation when the canonical query fails despite a legacy default", () => {
    setupMocks({
      defaultModel: "gpt-5.5",
      defaultModelSelectionError: new Error("request failed"),
    })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Unable to load models" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
  })

  it("waits for the canonical query even when a legacy default is available", () => {
    setupMocks({
      defaultModel: "gpt-5.5",
      defaultModelSelectionLoading: true,
    })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Loading models" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
  })

  it("does not query models while the dialog is closed", () => {
    setupMocks()
    render(<CreateAgentDialog open={false} onOpenChange={mockOnOpenChange} />)

    expect(useWorkspaceAgentModels).not.toHaveBeenCalled()
    expect(useAgentDefaultModel).not.toHaveBeenCalled()
    expect(useUserScopes).not.toHaveBeenCalled()
  })

  it("uses the configured custom-provider default model and preserves its base URL", async () => {
    const user = userEvent.setup()
    setupMocks({
      defaultModelSelection: {
        catalog_id: "catalog-fallback",
        model_name: "custom-fast",
        model_provider: "custom",
        custom_provider_id: "provider-custom",
      },
    })
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

  it.each([
    ["fails", { defaultModelError: new Error("request failed") }],
    ["is loading", { defaultModelLoading: true }],
    ["returns a stale model name", { defaultModel: "gpt-5.5" }],
  ] as const)(
    "shows setup guidance when the canonical default is null and the legacy query %s",
    (_label, legacyState) => {
      setupMocks({ defaultModelSelection: null, ...legacyState })
      renderCreateAgentDialog()

      expect(
        screen.getByRole("heading", { name: "Set up model provider" })
      ).toBeInTheDocument()
      expect(
        screen.getByRole("link", { name: "Configure models" })
      ).toHaveAttribute("href", "/organization/settings/agent")
      expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Create agent" })
      ).not.toBeInTheDocument()
      expect(mockCreateAgentPreset).not.toHaveBeenCalled()
    }
  )
})
