import { render, screen } from "@testing-library/react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ChatInterface } from "@/components/chat/chat-interface"
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
