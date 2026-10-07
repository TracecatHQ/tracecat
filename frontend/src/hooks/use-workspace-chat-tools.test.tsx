import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { type AgentSessionReadVercel, workspacesGetWorkspace } from "@/client"
import { useWorkspaceChatTools } from "@/hooks/use-workspace-chat-tools"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({ workspacesGetWorkspace: jest.fn() }))
jest.mock("@/lib/hooks", () => ({
  useUserScopes: () => ({
    userScopes: { scopes: ["action:core.read:execute"] },
    isLoading: false,
  }),
}))
const mockUpdateChat = jest.fn()
jest.mock("@/hooks/use-chat", () => ({
  useUpdateChat: () => ({ updateChat: mockUpdateChat, isUpdating: false }),
}))
jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: () => ({
    presets: [
      {
        id: "helper",
        current_version_subagent_eligibility: { eligible: true },
      },
      {
        id: "nested",
        current_version_subagent_eligibility: { eligible: false },
      },
    ],
    presetsIsLoading: false,
  }),
}))

it("inherits new workspace defaults, preserves explicit empty choices, and resets", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  jest.mocked(workspacesGetWorkspace).mockResolvedValue({
    id: "workspace",
    name: "Workspace",
    organization_id: "organization",
    settings: {
      chat: { tools: { mode: "all" } },
      effective_allowed_attachment_extensions: [],
      effective_allowed_attachment_mime_types: [],
    },
  })
  const actions = ["core.read", "core.write"].map((action) => ({
    id: action,
    name: action,
    action,
    namespace: "core",
    type: "udf" as const,
    origin: "platform",
    description: action,
  }))
  const { result } = renderHook(
    () =>
      useWorkspaceChatTools({
        workspaceId: "workspace",
        enabled: true,
        registryActions: actions,
        mcpIntegrations: [],
      }),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    }
  )

  await waitFor(() => expect(result.current.disabled).toBe(false))
  expect(result.current.selectedTools).toEqual(["core.read"])
  expect(result.current.selectedSubagents).toEqual(["helper"])
  act(() => result.current.onSubagentsChange([]))
  expect(result.current.selectedSubagents).toEqual([])
  act(() => result.current.onReset())
  expect(result.current.selectedSubagents).toEqual(["helper"])
  act(() => result.current.onToolsChange([]))
  expect(result.current.selectedTools).toEqual([])
  expect(result.current.overrides).toEqual({ tools: [] })
  act(() => result.current.onReset())
  expect(result.current.selectedTools).toEqual(["core.read"])
  expect(result.current.overrides).toBeNull()

  act(() =>
    queryClient.setQueryData(["workspace", "workspace"], {
      settings: {
        chat: {
          tools: { mode: "none" },
          subagents: { mode: "selected", selected: [] },
        },
      },
    })
  )
  await waitFor(() => expect(result.current.selectedTools).toEqual([]))
  act(() => result.current.onToolsChange(["core.read", "core.write"]))
  expect(result.current.selectedTools).toEqual([])
  act(() => result.current.onSubagentsChange(["helper", "nested", "foreign"]))
  expect(result.current.selectedSubagents).toEqual([])
})

it("saves stored chat overrides and drops a stale draft on chat switch", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  jest.mocked(workspacesGetWorkspace).mockResolvedValue({
    id: "workspace",
    name: "Workspace",
    organization_id: "organization",
    settings: {
      chat: { tools: { mode: "all" } },
      effective_allowed_attachment_extensions: [],
      effective_allowed_attachment_mime_types: [],
    },
  })
  const actions = ["core.read"].map((action) => ({
    id: action,
    name: action,
    action,
    namespace: "core",
    type: "udf" as const,
    origin: "platform",
    description: action,
  }))
  const storedChat = {
    id: "chat-1",
    workspace_chat_overrides: { tools: ["core.read"], mcp_integrations: null },
  } as unknown as AgentSessionReadVercel
  const { result, rerender } = renderHook(
    ({ chat }: { chat?: AgentSessionReadVercel }) =>
      useWorkspaceChatTools({
        workspaceId: "workspace",
        chat,
        enabled: true,
        registryActions: actions,
        mcpIntegrations: [],
      }),
    {
      initialProps: {},
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    }
  )

  await waitFor(() => expect(result.current.disabled).toBe(false))
  act(() => result.current.onToolsChange([]))
  expect(result.current.overrides).toEqual({ tools: [] })

  rerender({ chat: storedChat })
  expect(result.current.selectedTools).toEqual(["core.read"])
  await act(() => result.current.onToolsChange([]))
  expect(mockUpdateChat).toHaveBeenCalledWith({
    chatId: "chat-1",
    update: {
      workspace_chat_overrides: { tools: [], mcp_integrations: null },
    },
  })

  rerender({})
  expect(result.current.overrides).toBeNull()
  expect(result.current.selectedTools).toEqual(["core.read"])
})
