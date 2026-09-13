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
const mockHasEntitlement = jest.fn<boolean, [string]>(() => false)

jest.mock("next/navigation", () => ({
  useRouter: jest.fn(),
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

type SetupMocksOptions = {
  orgScopes?: string[]
  defaultModel?: string | null
  defaultModelSelection?: DefaultModelSelection | null
  defaultModelLoading?: boolean
  defaultModelError?: Error | null
  models?: typeof catalogModels
  modelsLoading?: boolean
  modelsError?: ApiError | null
}

function setupMocks({
  orgScopes = ["org:update"],
  defaultModel = null,
  defaultModelSelection = null,
  models = catalogModels,
  defaultModelLoading = false,
  defaultModelError = null,
  modelsLoading = false,
  modelsError = null,
}: SetupMocksOptions = {}) {
  jest.mocked(useUserScopes).mockReturnValue({
    userScopes: { scopes: orgScopes },
    isLoading: false,
    error: null,
  })
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
    providers: customProviders,
    modelsLoading,
    modelsError,
  })
  jest.mocked(useAgentDefaultModel).mockReturnValue({
    defaultModel,
    defaultModelSelection,
    defaultModelLoading,
    defaultModelError,
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
    setupMocks({ defaultModel: "custom-fast" })
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
    setupMocks({ defaultModel: "custom-fast" })
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
      screen.getByRole("heading", { name: "Set up model provider" })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument()
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
    setupMocks({ defaultModelLoading: true })
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

  it("shows a neutral error state when the default model query fails", () => {
    setupMocks({ defaultModelError: new Error("request failed") })
    renderCreateAgentDialog()

    expect(
      screen.getByRole("heading", { name: "Unable to load models" })
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

  it("keeps the create form available while the default is configured by legacy name", () => {
    setupMocks({ defaultModel: "gpt-5.5" })
    renderCreateAgentDialog()

    expect(screen.getByLabelText("Name")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Create agent" })
    ).toBeInTheDocument()
  })
})
