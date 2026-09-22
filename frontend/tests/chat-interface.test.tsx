import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ComponentProps } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ChatInterface } from "@/components/chat/chat-interface"
import { useAgentBackends } from "@/hooks/use-chat"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/components/chat/chat-session-pane", () => ({
  ChatSessionPane: ({
    mcpEnabled,
    onBeforeSend,
  }: {
    mcpEnabled: boolean
    onBeforeSend?: (message: string) => Promise<string | null>
  }) => (
    <div data-mcp-enabled={String(mcpEnabled)} data-testid="chat-session-pane">
      {onBeforeSend && (
        <button type="button" onClick={() => void onBeforeSend("Hello")}>
          Send first message
        </button>
      )}
    </div>
  ),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { id: "user-1" } }),
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: () => false,
    hasEntitlementData: true,
  }),
}))
const mockCreateChat = jest.fn()
const mockListChats = jest.fn()
const mockGetChat = jest.fn()
jest.mock("@/hooks/use-feature-flags", () => ({ useFeatureFlag: jest.fn() }))
jest.mock("@/hooks/use-chat", () => ({
  useAgentBackends: jest.fn(),
  useListChats: () => ({
    chats: mockListChats(),
    chatsLoading: false,
    chatsError: null,
  }),
  useCreateChat: () => ({
    createChat: mockCreateChat,
    createChatPending: false,
  }),
  useGetChatVercel: () => ({
    chat: mockGetChat(),
    chatLoading: false,
    chatError: null,
  }),
  useUpdateChat: () => ({ updateChat: jest.fn(), isUpdating: false }),
  parseChatError: (error: unknown) => String(error),
}))
jest.mock("@/hooks/use-chat-preset-manager", () => ({
  useChatPresetManager: () => ({
    selectedPreset: undefined,
    selectedPresetConfig: null,
    selectedPresetConfigError: undefined,
    selectedPresetVersionIsLoading: false,
    selectedPresetId: null,
    selectedPresetVersionId: null,
    handlePresetChange: jest.fn(),
    getPendingPresetSelection: () => ({
      presetId: null,
      versionId: null,
    }),
    presetMenuLabel: "Agents",
    presetMenuDisabled: false,
    showPresetSpinner: false,
  }),
}))
jest.mock("@/lib/hooks", () => ({
  useChatReadiness: () => ({
    ready: true,
    loading: false,
    modelInfo: { name: "gpt-test", provider: "openai", baseUrl: null },
  }),
}))
jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceMembers: () => ({ members: [] }),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))

const mockUseScopeCheck = useScopeCheck as jest.MockedFunction<
  typeof useScopeCheck
>

const mockUseAgentBackends = jest.mocked(useAgentBackends)
const mockUseFeatureFlag = jest.mocked(useFeatureFlag)
const multipleBackends = [
  { id: "oss", name: "Open source", capabilities: [] },
  { id: "ee", name: "Enterprise", capabilities: [] },
]

beforeEach(() => {
  jest.clearAllMocks()
  mockListChats.mockReturnValue([])
  mockGetChat.mockReturnValue(undefined)
  mockCreateChat.mockResolvedValue({ id: "chat-1" })
  mockUseFeatureFlag.mockReturnValue({
    isFeatureEnabled: () => false,
    isLoading: false,
    hasFeatureData: true,
  })
  mockUseAgentBackends.mockReturnValue({
    backendsLoading: false,
    backends: [
      {
        id: "oss",
        name: "Open source",
        capabilities: ["fork", "caller_owned_workflows"],
      },
    ],
  })
})

/** Drive `useScopeCheck` per scope so a single missing scope can be tested. */
function setScopeResult(scope: string, result: boolean | undefined) {
  mockUseScopeCheck.mockImplementation((requested) =>
    requested === scope ? result : true
  )
}

function renderChat(props: Partial<ComponentProps<typeof ChatInterface>> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatInterface
        entityId="workspace-1"
        entityType="copilot"
        surface="workspace-chat"
        {...props}
      />
    </QueryClientProvider>
  )
}

function mcpEnabled() {
  return screen.getByTestId("chat-session-pane").dataset.mcpEnabled
}

describe("ChatInterface MCP gating", () => {
  beforeEach(() => {
    mockUseScopeCheck.mockReset()
  })

  it("enables MCP for a copilot session when the role has integration:read", () => {
    setScopeResult("integration:read", true)
    renderChat()
    expect(mcpEnabled()).toBe("true")
  })

  it("disables MCP when the role is missing integration:read", () => {
    setScopeResult("integration:read", false)
    renderChat()
    expect(mcpEnabled()).toBe("false")
  })

  it("keeps MCP off while scopes are still loading", () => {
    setScopeResult("integration:read", undefined)
    renderChat()
    expect(mcpEnabled()).toBe("false")
  })
})

describe("ChatInterface backend selection", () => {
  beforeEach(() => {
    setScopeResult("integration:read", true)
  })

  it("hides the selector when only the built-in mode is available", () => {
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: (flag) => flag === "agent-runtime",
      isLoading: false,
      hasFeatureData: true,
    })
    renderChat()
    expect(
      screen.queryByRole("combobox", { name: "Backend (dev)" })
    ).not.toBeInTheDocument()
  })

  it("keeps backend selection hidden and uses the server default without the flag", async () => {
    mockUseAgentBackends.mockReturnValue({
      backends: multipleBackends,
      backendsLoading: false,
    })
    renderChat({ surface: "regular" })
    expect(
      screen.queryByRole("combobox", { name: "Backend (dev)" })
    ).not.toBeInTheDocument()
    expect(mockUseAgentBackends).toHaveBeenCalledWith("workspace-1", {
      enabled: false,
    })
    await waitFor(() => expect(mockCreateChat).toHaveBeenCalledTimes(1))
    expect(mockCreateChat.mock.calls[0][0].backend_id).toBeUndefined()
    expect(screen.queryByText("Open source")).not.toBeInTheDocument()
  })

  it("waits for feature flags before auto-creating a sidebar chat", () => {
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: () => false,
      isLoading: true,
      hasFeatureData: false,
    })
    renderChat({ surface: "regular" })
    expect(mockCreateChat).not.toHaveBeenCalled()
  })

  it("hides the active backend badge without the flag", () => {
    mockGetChat.mockReturnValue({ id: "chat-1", backend_id: "oss" })
    renderChat({ chatId: "chat-1" })
    expect(screen.queryByText("Open source")).not.toBeInTheDocument()
  })

  it("keeps a new flagged sidebar draft open when prior chats exist", async () => {
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: (flag) => flag === "agent-runtime",
      isLoading: false,
      hasFeatureData: true,
    })
    mockUseAgentBackends.mockReturnValue({
      backends: multipleBackends,
      backendsLoading: false,
    })
    mockListChats.mockReturnValue([
      {
        id: "chat-1",
        title: "Existing chat",
        created_at: "2026-01-01T00:00:00Z",
      },
    ])
    mockGetChat.mockReturnValue({ id: "chat-1", backend_id: "oss" })
    renderChat({ surface: "regular" })
    expect(screen.getByText("Open source")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "New chat" }))
    fireEvent.click(screen.getByRole("button", { name: "Start new chat" }))
    expect(
      await screen.findByRole("button", { name: "Send first message" })
    ).toBeInTheDocument()
    expect(mockCreateChat).not.toHaveBeenCalled()
  })

  it("waits for backend discovery before auto-creating a sidebar chat", () => {
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: (flag) => flag === "agent-runtime",
      isLoading: false,
      hasFeatureData: true,
    })
    mockUseAgentBackends.mockReturnValue({
      backends: [],
      backendsLoading: true,
    })
    renderChat({ surface: "regular" })
    expect(mockCreateChat).not.toHaveBeenCalled()
  })

  it("lets a flagged sidebar choose a backend before its first message", async () => {
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: (flag) => flag === "agent-runtime",
      isLoading: false,
      hasFeatureData: true,
    })
    mockUseAgentBackends.mockReturnValue({
      backends: multipleBackends,
      backendsLoading: false,
    })
    renderChat({ surface: "regular" })
    const selector = screen.getByRole("combobox", { name: "Backend (dev)" })
    expect(selector).toHaveTextContent("Server default")
    expect(mockCreateChat).not.toHaveBeenCalled()
    fireEvent.keyDown(selector, { key: "ArrowDown" })
    fireEvent.click(await screen.findByRole("option", { name: "Enterprise" }))
    expect(mockCreateChat).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Send first message" }))
    await waitFor(() => expect(mockCreateChat).toHaveBeenCalledTimes(1))
    expect(mockCreateChat).toHaveBeenCalledWith(
      expect.objectContaining({ backend_id: "ee" })
    )
  })
})
