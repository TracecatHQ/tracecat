import { render, screen } from "@testing-library/react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ChatInterface } from "@/components/chat/chat-interface"
import { useAgentBackends } from "@/hooks/use-chat"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/components/chat/chat-session-pane", () => ({
  ChatSessionPane: ({ mcpEnabled }: { mcpEnabled: boolean }) => (
    <div
      data-mcp-enabled={String(mcpEnabled)}
      data-testid="chat-session-pane"
    />
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
jest.mock("@/hooks/use-chat", () => ({
  useAgentBackends: jest.fn(),
  useListChats: () => ({ chats: [], chatsLoading: false, chatsError: null }),
  useCreateChat: () => ({
    createChat: jest.fn(),
    createChatPending: false,
  }),
  useGetChatVercel: () => ({
    chat: undefined,
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

beforeEach(() => {
  mockUseAgentBackends.mockReturnValue({
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

function renderChat() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatInterface
        entityId="workspace-1"
        entityType="copilot"
        surface="workspace-chat"
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

describe("ChatInterface mode selection", () => {
  it("hides the selector when only the built-in mode is available", () => {
    setScopeResult("integration:read", true)
    renderChat()
    expect(
      screen.queryByRole("combobox", { name: "Chat mode" })
    ).not.toBeInTheDocument()
  })

  it("shows a product label when another backend is installed", () => {
    setScopeResult("integration:read", true)
    mockUseAgentBackends.mockReturnValue({
      backends: [
        {
          id: "oss",
          name: "Open source",
          capabilities: ["fork", "caller_owned_workflows"],
        },
        { id: "ee", name: "Enterprise", capabilities: [] },
      ],
    })
    renderChat()
    const selectors = screen.getAllByRole("combobox", { name: "Chat mode" })
    expect(selectors.length).toBeGreaterThan(0)
    expect(selectors[0]).toHaveTextContent("Open source")
    expect(screen.queryByText("claude_code")).not.toBeInTheDocument()
  })
})
