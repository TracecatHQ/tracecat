import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ComponentProps } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ChatInterface } from "@/components/chat/chat-interface"
import { useAgentBackends } from "@/hooks/use-chat"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/components/chat/chat-session-pane", () => ({
  AGENT_EXECUTE_REQUIRED_MESSAGE: "agent:execute required",
  ChatSessionPane: ({
    chat,
    mcpEnabled,
    onBeforeSend,
    inputDisabled,
  }: {
    chat?: { id: string }
    mcpEnabled: boolean
    onBeforeSend?: (message: string) => Promise<string | null>
    inputDisabled?: boolean
  }) => (
    <div
      data-mcp-enabled={String(mcpEnabled)}
      data-input-disabled={String(Boolean(inputDisabled))}
      data-testid="chat-session-pane"
    >
      {chat && <p>Transcript of {chat.id}</p>}
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
const mockUseEntitlements = jest.fn()
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: (options?: { enabled?: boolean }) =>
    mockUseEntitlements(options),
}))
const mockCreateChat = jest.fn()
const mockListChats = jest.fn()
const mockGetChat = jest.fn()
const mockGetChatError = jest.fn()
const mockRefetchChat = jest.fn()
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
    chatFetching: false,
    chatError: mockGetChatError(),
    refetchChat: mockRefetchChat,
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
const mockUseChatReadiness = jest.fn()
jest.mock("@/lib/hooks", () => ({
  useChatReadiness: () => mockUseChatReadiness(),
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
const mockRefetchBackends = jest.fn()
const discoveryReady = {
  backendsReady: true,
  backendsError: null,
  refetchBackends: mockRefetchBackends,
}
const multipleBackends = [
  { id: "oss", name: "Open source" },
  { id: "ee", name: "Enterprise" },
]

beforeEach(() => {
  jest.clearAllMocks()
  mockUseChatReadiness.mockReturnValue({
    ready: true,
    loading: false,
    modelInfo: { name: "gpt-test", provider: "openai", baseUrl: null },
  })
  mockListChats.mockReturnValue([])
  mockGetChat.mockReturnValue(undefined)
  mockGetChatError.mockReturnValue(null)
  mockCreateChat.mockResolvedValue({ id: "chat-1" })
  mockUseEntitlements.mockReturnValue({
    hasEntitlement: (key: string) => key === "agent_runtime_preview",
    isLoading: false,
    hasEntitlementData: true,
  })
  mockUseFeatureFlag.mockReturnValue({
    isFeatureEnabled: () => false,
    isLoading: false,
    hasFeatureData: true,
  })
  mockUseAgentBackends.mockReturnValue({
    ...discoveryReady,
    backendsLoading: false,
    backends: [
      {
        id: "oss",
        name: "Open source",
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
    <ChatInterface
      entityId="workspace-1"
      entityType="copilot"
      surface="workspace-chat"
      {...props}
    />,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    }
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
      ...discoveryReady,
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

  it("keeps backend selection hidden without the entitlement", async () => {
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: (flag) => flag === "agent-runtime",
      isLoading: false,
      hasFeatureData: true,
    })
    mockUseEntitlements.mockReturnValue({
      hasEntitlement: () => false,
      isLoading: false,
      hasEntitlementData: true,
    })
    mockUseAgentBackends.mockReturnValue({
      ...discoveryReady,
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
  })

  it("waits for entitlements before auto-creating a flagged sidebar chat", () => {
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: (flag) => flag === "agent-runtime",
      isLoading: false,
      hasFeatureData: true,
    })
    mockUseEntitlements.mockReturnValue({
      hasEntitlement: () => false,
      isLoading: true,
      hasEntitlementData: false,
    })
    renderChat({ surface: "regular" })
    expect(mockCreateChat).not.toHaveBeenCalled()
  })

  it("skips the entitlement lookup without the flag", () => {
    renderChat({ surface: "regular" })
    expect(mockUseEntitlements).toHaveBeenCalledWith({ enabled: false })
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
      ...discoveryReady,
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
      ...discoveryReady,
      backendsReady: false,
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
      ...discoveryReady,
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

  it.each([false, true])(
    "falls back after the selected backend disappears (selector visible: %s)",
    async (keepChoice) => {
      mockUseFeatureFlag.mockReturnValue({
        isFeatureEnabled: (flag) => flag === "agent-runtime",
        isLoading: false,
        hasFeatureData: true,
      })
      mockUseAgentBackends.mockReturnValue({
        ...discoveryReady,
        backends: multipleBackends,
        backendsLoading: false,
      })
      const { rerender } = renderChat({ surface: "regular" })
      fireEvent.keyDown(
        screen.getByRole("combobox", { name: "Backend (dev)" }),
        {
          key: "ArrowDown",
        }
      )
      fireEvent.click(await screen.findByRole("option", { name: "Enterprise" }))
      mockUseAgentBackends.mockReturnValue({
        ...discoveryReady,
        backends: keepChoice
          ? [multipleBackends[0], { id: "another", name: "Another backend" }]
          : [multipleBackends[0]],
        backendsLoading: false,
      })
      rerender(
        <ChatInterface
          entityId="workspace-1"
          entityType="copilot"
          surface="regular"
        />
      )
      if (keepChoice) {
        expect(
          screen.getByRole("combobox", { name: "Backend (dev)" })
        ).toHaveTextContent("Server default")
        fireEvent.click(
          screen.getByRole("button", { name: "Send first message" })
        )
      } else {
        expect(
          screen.queryByRole("combobox", { name: "Backend (dev)" })
        ).not.toBeInTheDocument()
      }
      await waitFor(() => expect(mockCreateChat).toHaveBeenCalledTimes(1))
      expect(mockCreateChat.mock.calls[0][0].backend_id).toBeUndefined()
    }
  )

  it.each(["regular", "workspace-chat"] as const)(
    "blocks new sessions on discovery failure on the %s surface",
    (surface) => {
      mockUseFeatureFlag.mockReturnValue({
        isFeatureEnabled: (flag) => flag === "agent-runtime",
        isLoading: false,
        hasFeatureData: true,
      })
      mockUseAgentBackends.mockReturnValue({
        ...discoveryReady,
        backends: [],
        backendsLoading: false,
        backendsReady: false,
        backendsError: new Error("Discovery failed"),
      })
      renderChat({ surface })
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Unable to load chat backends"
      )
      expect(screen.getByRole("button", { name: "New chat" })).toBeDisabled()
      // The mock pane deliberately invokes the callback even when disabled.
      const sendButton = screen.queryByRole("button", {
        name: "Send first message",
      })
      if (sendButton) fireEvent.click(sendButton)
      expect(mockCreateChat).not.toHaveBeenCalled()
      fireEvent.click(screen.getByRole("button", { name: "Retry" }))
      expect(mockRefetchBackends).toHaveBeenCalledTimes(1)
    }
  )

  it("allows backend selection after discovery recovers", async () => {
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: (flag) => flag === "agent-runtime",
      isLoading: false,
      hasFeatureData: true,
    })
    mockUseAgentBackends.mockReturnValue({
      ...discoveryReady,
      backends: [],
      backendsLoading: false,
      backendsReady: false,
      backendsError: new Error("Discovery failed"),
    })
    const { rerender } = renderChat({ surface: "regular" })
    expect(mockCreateChat).not.toHaveBeenCalled()
    mockUseAgentBackends.mockReturnValue({
      ...discoveryReady,
      backends: multipleBackends,
      backendsLoading: false,
    })
    rerender(
      <ChatInterface
        entityId="workspace-1"
        entityType="copilot"
        surface="regular"
      />
    )
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(mockCreateChat).not.toHaveBeenCalled()
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Backend (dev)" }), {
      key: "ArrowDown",
    })
    fireEvent.click(await screen.findByRole("option", { name: "Enterprise" }))
    fireEvent.click(screen.getByRole("button", { name: "Send first message" }))
    await waitFor(() => expect(mockCreateChat).toHaveBeenCalledTimes(1))
    expect(mockCreateChat).toHaveBeenCalledWith(
      expect.objectContaining({ backend_id: "ee" })
    )
  })

  it("keeps existing chats accessible when discovery fails", () => {
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: (flag) => flag === "agent-runtime",
      isLoading: false,
      hasFeatureData: true,
    })
    mockUseAgentBackends.mockReturnValue({
      ...discoveryReady,
      backends: [],
      backendsLoading: false,
      backendsReady: false,
      backendsError: new Error("Discovery failed"),
    })
    mockListChats.mockReturnValue([
      {
        id: "chat-1",
        title: "Existing chat",
        created_at: "2026-01-01T00:00:00Z",
      },
    ])
    mockGetChat.mockReturnValue({ id: "chat-1", backend_id: "oss" })
    const onChatSelect = jest.fn()
    renderChat({ surface: "regular", onChatSelect })
    expect(onChatSelect).toHaveBeenCalledWith("chat-1")
    expect(screen.getByTestId("chat-session-pane")).toBeInTheDocument()
    expect(mockCreateChat).not.toHaveBeenCalled()
  })
})

describe("ChatInterface agent:execute gating", () => {
  beforeEach(() => {
    mockUseScopeCheck.mockReset()
  })

  it("does not create a chat without agent:execute and says why", async () => {
    setScopeResult("agent:execute", false)
    renderChat({ surface: "regular" })

    expect(
      await screen.findByText("agent:execute required")
    ).toBeInTheDocument()
    expect(mockCreateChat).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "New chat" })).toBeDisabled()
  })

  it("auto-creates a chat with agent:execute", async () => {
    setScopeResult("agent:execute", true)
    renderChat({ surface: "regular" })

    await waitFor(() => expect(mockCreateChat).toHaveBeenCalledTimes(1))
    expect(screen.queryByText("agent:execute required")).not.toBeInTheDocument()
  })

  it("waits for scopes to load before creating a chat", async () => {
    setScopeResult("agent:execute", undefined)
    const view = renderChat({ surface: "regular" })

    // Loading scopes hold creation and show no denial.
    expect(mockCreateChat).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "New chat" })).toBeDisabled()
    expect(screen.queryByText("agent:execute required")).not.toBeInTheDocument()

    setScopeResult("agent:execute", true)
    view.rerender(
      <ChatInterface
        entityId="workspace-1"
        entityType="copilot"
        surface="regular"
      />
    )

    await waitFor(() => expect(mockCreateChat).toHaveBeenCalledTimes(1))
  })

  it("holds the first message of a draft chat until agent:execute resolves", async () => {
    // A deferred chat creates its session on the first send.
    mockUseFeatureFlag.mockReturnValue({
      isFeatureEnabled: (flag) => flag === "agent-runtime",
      isLoading: false,
      hasFeatureData: true,
    })
    mockUseAgentBackends.mockReturnValue({
      ...discoveryReady,
      backends: multipleBackends,
      backendsLoading: false,
    })
    setScopeResult("agent:execute", undefined)
    const view = renderChat({ surface: "regular" })

    fireEvent.click(
      await screen.findByRole("button", { name: "Send first message" })
    )
    expect(mockCreateChat).not.toHaveBeenCalled()

    setScopeResult("agent:execute", true)
    view.rerender(
      <ChatInterface
        entityId="workspace-1"
        entityType="copilot"
        surface="regular"
      />
    )
    fireEvent.click(screen.getByRole("button", { name: "Send first message" }))
    await waitFor(() => expect(mockCreateChat).toHaveBeenCalledTimes(1))
  })
})

describe("ChatInterface model readiness", () => {
  beforeEach(() => {
    mockUseScopeCheck.mockReset()
    setScopeResult("agent:execute", true)
  })

  it("says the default is not enabled here instead of linking to org settings", () => {
    // Changing the org default cannot grant this workspace access to it.
    mockUseChatReadiness.mockReturnValue({
      ready: false,
      loading: false,
      reason: "default_not_enabled",
    })
    renderChat({ surface: "regular" })

    expect(screen.getByText("Default model not enabled")).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: /No default model/ })
    ).not.toBeInTheDocument()
  })

  it("says the org disabled the workspace's models when none are left", () => {
    // A subset whose models the org disabled leaves the workspace with none.
    mockUseChatReadiness.mockReturnValue({
      ready: false,
      loading: false,
      reason: "subset_disabled",
    })
    renderChat({ surface: "regular" })

    expect(screen.getByText("Workspace models disabled")).toBeInTheDocument()
    expect(
      screen.getByText(/organization admin can enable more models/)
    ).toBeInTheDocument()
  })

  it("keeps an existing chat's transcript when no model is usable", () => {
    // Only starting or continuing a chat needs a model; history stays readable.
    mockUseChatReadiness.mockReturnValue({
      ready: false,
      loading: false,
      reason: "default_not_enabled",
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

    expect(screen.getByText("Transcript of chat-1")).toBeInTheDocument()
    expect(screen.getByTestId("chat-session-pane")).toHaveAttribute(
      "data-input-disabled",
      "true"
    )
    expect(
      screen.queryByText("Default model not enabled")
    ).not.toBeInTheDocument()
  })

  it("shows the role denial instead of model setup without agent:execute", () => {
    // Model setup is not the viewer's to fix.
    setScopeResult("agent:execute", false)
    mockUseChatReadiness.mockReturnValue({
      ready: false,
      loading: false,
      reason: "no_model",
    })
    renderChat({ surface: "regular" })

    expect(screen.getByText("agent:execute required")).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: /No default model/ })
    ).not.toBeInTheDocument()
  })

  it("links to agent settings when the org has no default model", () => {
    mockUseChatReadiness.mockReturnValue({
      ready: false,
      loading: false,
      reason: "no_model",
    })
    renderChat({ surface: "regular" })

    expect(
      screen.getByRole("link", { name: /No default model/ })
    ).toHaveAttribute("href", "/organization/settings/agent")
  })
})

describe("ChatInterface session fetch errors", () => {
  beforeEach(() => {
    mockUseScopeCheck.mockReturnValue(true)
  })

  it("keeps the loaded transcript when a session refetch fails", () => {
    mockGetChat.mockReturnValue({ id: "chat-1", backend_id: "oss" })
    mockGetChatError.mockReturnValue(new Error("boom"))
    renderChat({ chatId: "chat-1" })

    expect(screen.getByText("Transcript of chat-1")).toBeInTheDocument()
    expect(screen.queryByText("Failed to load chat")).not.toBeInTheDocument()
  })

  it("offers a retry when the session never loaded", () => {
    mockGetChatError.mockReturnValue(new Error("boom"))
    renderChat({ chatId: "chat-1" })

    expect(screen.getByText("Failed to load chat")).toBeInTheDocument()
    expect(screen.getByLabelText("New chat")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(mockRefetchChat).toHaveBeenCalledTimes(1)
  })
})
