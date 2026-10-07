import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { agentFoldersGetFolder } from "@/client"
import { AgentsDashboard } from "@/components/agents/agents-dashboard"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const mockHasEntitlement = jest.fn<boolean, [string]>(() => false)
const mockSearchParams = { current: new URLSearchParams() }

const mockUseAgentPresets = jest.fn()
const mockUseAgentDirectoryItems = jest.fn()
const mockUseAgentTagCatalog = jest.fn()
const mockUseAgentFolders = jest.fn()
const mockUseWorkspaceDetails = jest.fn()
const mockUseAgentPreset = jest.fn()
const mockSetDefaultAgent = jest.fn()
const mockUpdateAgentPreset = jest.fn()
const mockUseScopeCheck = jest.fn<boolean, [string]>(() => true)
const mockPush = jest.fn()

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  agentFoldersGetFolder: jest.fn(),
}))

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceDetails: () => mockUseWorkspaceDetails(),
}))

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
  useSearchParams: () => mockSearchParams.current,
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => mockHasEntitlement(key),
    isLoading: false,
    hasEntitlementData: true,
  }),
}))

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: (...args: unknown[]) => mockUseAgentPresets(...args),
  useAgentDirectoryItems: (...args: unknown[]) =>
    mockUseAgentDirectoryItems(...args),
  useAgentTagCatalog: (...args: unknown[]) => mockUseAgentTagCatalog(...args),
  useAgentFolders: (...args: unknown[]) => mockUseAgentFolders(...args),
  useAgentPreset: (...args: unknown[]) => mockUseAgentPreset(...args),
  useSetDefaultAgent: () => ({
    setDefaultAgent: mockSetDefaultAgent,
    isSettingDefaultAgent: false,
  }),
  useUpdateAgentPreset: () => ({
    updateAgentPreset: mockUpdateAgentPreset,
    updateAgentPresetIsPending: false,
  }),
  useCreateAgentPreset: () => ({
    createAgentPreset: jest.fn(),
    createAgentPresetIsPending: false,
  }),
  useDeleteAgentPreset: () => ({
    deleteAgentPreset: jest.fn(),
    deleteAgentPresetIsPending: false,
  }),
  useMoveAgentPreset: () => ({
    moveAgentPreset: jest.fn(),
    moveAgentPresetIsPending: false,
  }),
}))

const PRESET = {
  id: "preset-1",
  name: "Legacy preset",
  slug: "legacy-preset",
  description: null,
  use_in_chat: false,
  model_provider: "openai",
  model_name: "gpt-test",
  folder_id: "folder-1",
  tags: [{ id: "tag-1", name: "legacy", ref: "legacy", color: "#000" }],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

function renderDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AgentsDashboard />
    </QueryClientProvider>
  )
}

function lastOptions(mock: jest.Mock): { enabled?: boolean } | undefined {
  const call = mock.mock.calls.at(-1)
  return call?.at(-1) as { enabled?: boolean } | undefined
}

describe("AgentsDashboard entitlement split", () => {
  beforeEach(() => {
    mockUseWorkspaceDetails.mockReturnValue({ workspace: { settings: {} } })
    mockUseAgentPreset.mockReturnValue({ preset: PRESET })
    mockSetDefaultAgent.mockClear()
    mockUpdateAgentPreset.mockReset()
    mockUpdateAgentPreset.mockResolvedValue(PRESET)
    mockUseScopeCheck.mockReset()
    mockUseScopeCheck.mockReturnValue(true)
    mockPush.mockClear()
    jest.mocked(agentFoldersGetFolder).mockReset()
    mockHasEntitlement.mockReset()
    mockHasEntitlement.mockReturnValue(false)
    mockSearchParams.current = new URLSearchParams()
    mockUseAgentPresets.mockReset()
    mockUseAgentPresets.mockReturnValue({
      presets: [PRESET],
      presetsIsLoading: false,
      presetsError: null,
    })
    mockUseAgentDirectoryItems.mockReset()
    mockUseAgentDirectoryItems.mockReturnValue({
      directoryItems: [],
      directoryItemsIsLoading: false,
      directoryItemsError: null,
    })
    mockUseAgentTagCatalog.mockReset()
    mockUseAgentTagCatalog.mockReturnValue({
      agentTags: [],
      agentTagsIsLoading: false,
    })
    mockUseAgentFolders.mockReset()
    mockUseAgentFolders.mockReturnValue({
      folders: [],
      foldersIsLoading: false,
    })
  })

  it("renders a flat catalog without folder/tag queries when unentitled", () => {
    mockSearchParams.current = new URLSearchParams("view=folders&path=/legacy/")

    renderDashboard()

    expect(lastOptions(mockUseAgentPresets)?.enabled).toBe(true)
    expect(lastOptions(mockUseAgentDirectoryItems)?.enabled).toBe(false)
    expect(lastOptions(mockUseAgentTagCatalog)?.enabled).toBe(false)
    expect(lastOptions(mockUseAgentFolders)?.enabled).toBe(false)

    expect(screen.getByText("Legacy preset")).toBeInTheDocument()
    expect(screen.queryByText("legacy")).not.toBeInTheDocument()
    expect(screen.queryByText("View")).not.toBeInTheDocument()
  })

  it("uses the folder directory and tag catalog when entitled", () => {
    mockHasEntitlement.mockImplementation((key) => key === "agent_addons")
    mockSearchParams.current = new URLSearchParams("view=folders")

    renderDashboard()

    expect(lastOptions(mockUseAgentPresets)?.enabled).toBe(false)
    expect(lastOptions(mockUseAgentDirectoryItems)?.enabled).toBe(true)
    expect(lastOptions(mockUseAgentTagCatalog)?.enabled).toBe(true)
    expect(screen.getByText("View")).toBeInTheDocument()
  })

  it("sets the default through the context menu without opening the agent", async () => {
    renderDashboard()
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "Make default" })
    )
    expect(mockSetDefaultAgent).toHaveBeenCalledWith(PRESET.id)
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("hides Make default without workspace update permission", () => {
    mockUseScopeCheck.mockImplementation(
      (scope) => scope !== "workspace:update"
    )
    renderDashboard()
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    expect(
      screen.queryByRole("menuitem", { name: "Make default" })
    ).not.toBeInTheDocument()
  })

  it.each([false, true])(
    "toggles chat visibility from %s without navigation",
    async (enabled) => {
      mockUseAgentPresets.mockReturnValue({
        presets: [{ ...PRESET, use_in_chat: enabled }],
        presetsIsLoading: false,
        presetsError: null,
      })
      renderDashboard()
      fireEvent.contextMenu(screen.getByText(PRESET.name))
      const toggle = await screen.findByRole("menuitemcheckbox", {
        name: "Use in chat",
      })
      expect(toggle).toHaveAttribute("aria-checked", String(enabled))
      await userEvent.click(toggle)
      expect(mockUpdateAgentPreset).toHaveBeenCalledWith({
        presetId: PRESET.id,
        use_in_chat: !enabled,
      })
      expect(mockPush).not.toHaveBeenCalled()
    }
  )

  it("hides the chat toggle without agent update permission", () => {
    mockUseScopeCheck.mockImplementation((scope) => scope !== "agent:update")
    renderDashboard()
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    expect(
      screen.queryByRole("menuitemcheckbox", { name: "Use in chat" })
    ).not.toBeInTheDocument()
  })

  it("reflects chat visibility in the folder view", async () => {
    mockHasEntitlement.mockReturnValue(true)
    mockSearchParams.current = new URLSearchParams("view=folders")
    mockUseAgentDirectoryItems.mockReturnValue({
      directoryItems: [{ ...PRESET, type: "preset", use_in_chat: true }],
      directoryItemsIsLoading: false,
      directoryItemsError: null,
    })
    renderDashboard()
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    const toggle = await screen.findByRole("menuitemcheckbox", {
      name: "Use in chat",
    })
    expect(toggle).toHaveAttribute("aria-checked", "true")
    await userEvent.click(toggle)
    expect(mockUpdateAgentPreset).toHaveBeenCalledWith({
      presetId: PRESET.id,
      use_in_chat: false,
    })
  })

  it("marks the default row and links the header badge to the preset", () => {
    mockUseWorkspaceDetails.mockReturnValue({
      workspace: { default_agent_preset_id: PRESET.id },
    })
    renderDashboard()
    expect(screen.getByText("Default")).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: `Default agent: ${PRESET.name}` })
    ).toHaveAttribute("href", `/workspaces/workspace-1/agents/${PRESET.id}`)
    fireEvent.contextMenu(screen.getByText(PRESET.name))
    expect(
      screen.getByRole("menuitem", { name: "Make default" })
    ).toHaveAttribute("data-disabled")
    expect(agentFoldersGetFolder).not.toHaveBeenCalled()
  })

  it("previews the default outside the current folder, including description and folder", async () => {
    mockHasEntitlement.mockReturnValue(true)
    mockSearchParams.current = new URLSearchParams("view=folders")
    mockUseWorkspaceDetails.mockReturnValue({
      workspace: { default_agent_preset_id: PRESET.id },
    })
    mockUseAgentPreset.mockReturnValue({
      preset: { ...PRESET, description: "Synthetic default agent description" },
    })
    jest.mocked(agentFoldersGetFolder).mockResolvedValue({
      id: "folder-1",
      name: "Nested",
      path: "/Parent/Nested/",
      workspace_id: "workspace-1",
      created_at: PRESET.created_at,
      updated_at: PRESET.updated_at,
    })
    renderDashboard()
    await userEvent.hover(
      screen.getByRole("link", { name: `Default agent: ${PRESET.name}` })
    )
    await waitFor(
      () => {
        expect(
          screen.getByText("Synthetic default agent description")
        ).toBeInTheDocument()
        expect(screen.getByText("/Parent/Nested/")).toBeInTheDocument()
        expect(
          screen.queryByText("Workspace default agent")
        ).not.toBeInTheDocument()
      },
      { timeout: 2000 }
    )
  })

  it("does not render a header badge when no default is configured", () => {
    renderDashboard()
    expect(
      screen.queryByRole("link", { name: /Default agent:/ })
    ).not.toBeInTheDocument()
    expect(screen.queryByText("Default")).not.toBeInTheDocument()
  })

  it("hides stale or deleted default details rather than linking to a missing preset", () => {
    mockUseWorkspaceDetails.mockReturnValue({
      workspace: { default_agent_preset_id: PRESET.id },
    })
    mockUseAgentPreset.mockReturnValue({
      preset: undefined,
      presetError: new Error("Not found"),
    })
    renderDashboard()
    expect(
      screen.queryByRole("link", { name: /Default agent:/ })
    ).not.toBeInTheDocument()
  })
})
