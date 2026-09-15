import { render, screen } from "@testing-library/react"
import { AgentsDashboard } from "@/components/agents/agents-dashboard"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const mockHasEntitlement = jest.fn<boolean, [string]>(() => false)
const mockSearchParams = { current: new URLSearchParams() }

const mockUseAgentPresets = jest.fn()
const mockUseAgentDirectoryItems = jest.fn()
const mockUseAgentTagCatalog = jest.fn()
const mockUseAgentFolders = jest.fn()

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => mockSearchParams.current,
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
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
})
