import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useRouter } from "next/navigation"
import type { ReactNode } from "react"
import type { DefaultModelSelection } from "@/client"
import { CreateAgentDialog } from "@/components/agents/create-agent-dialog"
import { toast } from "@/components/ui/use-toast"
import {
  useCreateAgentPreset,
  useMoveAgentPreset,
} from "@/hooks/use-agent-presets"
import { useAgentDefaultModel, useWorkspaceAgentModels } from "@/lib/hooks"

const mockCreateAgentPreset = jest.fn()
const mockMoveAgentPreset = jest.fn()
const mockOnOpenChange = jest.fn()
const mockRouterPush = jest.fn()
const mockRouterReplace = jest.fn()

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

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/hooks/use-agent-presets", () => ({
  useCreateAgentPreset: jest.fn(),
  useMoveAgentPreset: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  useAgentDefaultModel: jest.fn(),
  useWorkspaceAgentModels: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
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

function setupMocks({
  defaultModel = null,
  defaultModelSelection = null,
  models = catalogModels,
}: {
  defaultModel?: string | null
  defaultModelSelection?: DefaultModelSelection | null
  models?: typeof catalogModels
} = {}) {
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
    modelsLoading: false,
    modelsError: null,
  })
  jest.mocked(useAgentDefaultModel).mockReturnValue({
    defaultModel,
    defaultModelSelection,
    defaultModelLoading: false,
    defaultModelError: null,
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

function renderCreateAgentDialog() {
  render(<CreateAgentDialog open={true} onOpenChange={mockOnOpenChange} />)
}

describe("CreateAgentDialog", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockCreateAgentPreset.mockResolvedValue({
      id: "preset-1",
      name: "QA agent",
    })
    mockMoveAgentPreset.mockResolvedValue(undefined)
  })

  it("falls back to the first enabled workspace model when no default is set", async () => {
    const user = userEvent.setup()
    setupMocks()
    renderCreateAgentDialog()

    await user.type(screen.getByLabelText("Name"), "QA agent")
    await user.type(
      screen.getByLabelText("Description (optional)"),
      "Created during QA"
    )
    await user.click(screen.getByRole("button", { name: "Create agent" }))

    await waitFor(() => {
      expect(mockCreateAgentPreset).toHaveBeenCalledWith({
        name: "QA agent",
        model_provider: "custom",
        model_name: "custom-fast",
        catalog_id: "catalog-fallback",
        base_url: "https://models.example.com/v1",
        description: "Created during QA",
      })
      expect(mockOnOpenChange).toHaveBeenCalledWith(false)
      expect(mockRouterPush).toHaveBeenCalledWith(
        "/workspaces/workspace-1/agents/preset-1"
      )
    })
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

  it("shows a destructive toast when no workspace models are enabled", async () => {
    const user = userEvent.setup()
    setupMocks({ models: [] })
    renderCreateAgentDialog()

    await user.type(screen.getByLabelText("Name"), "No model agent")
    await user.click(screen.getByRole("button", { name: "Create agent" }))

    await waitFor(() => {
      expect(mockCreateAgentPreset).not.toHaveBeenCalled()
      expect(toast).toHaveBeenCalledWith({
        title: "Agent model required",
        description:
          "Enable an agent model in organization settings before creating an agent.",
        variant: "destructive",
      })
    })
  })
})
