import { render, screen } from "@testing-library/react"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { InboxDetail } from "@/components/inbox/inbox-detail"
import { useGetChatVercel } from "@/hooks/use-chat"
import type { InboxSessionItem } from "@/lib/agents"
import { useChatReadiness } from "@/lib/hooks"

jest.mock("@/components/chat/chat-session-pane", () => ({
  ChatSessionPane: ({
    resume,
    modelInfo,
  }: {
    resume?: boolean
    modelInfo?: { name: string; provider: string }
  }) => (
    <div
      data-testid="chat-session-pane"
      data-resume={String(resume)}
      data-model={modelInfo ? `${modelInfo.name} ${modelInfo.provider}` : ""}
    />
  ),
}))
jest.mock("@/hooks/use-chat", () => ({
  useGetChatVercel: jest.fn(),
}))
jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPreset: (_workspaceId: string, presetId?: string | null) => ({
    preset: presetId
      ? { model_name: "preset-current-model", model_provider: "anthropic" }
      : undefined,
    presetIsLoading: false,
  }),
  useAgentPresetVersion: (
    _workspaceId: string,
    presetId?: string | null,
    versionId?: string | null
  ) => ({
    presetVersion:
      presetId && versionId
        ? { model_name: "claude-sonnet", model_provider: "anthropic" }
        : undefined,
    presetVersionIsLoading: false,
  }),
}))
jest.mock("@/lib/hooks", () => ({
  useChatReadiness: jest.fn(),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const mockUseGetChatVercel = useGetChatVercel as jest.MockedFunction<
  typeof useGetChatVercel
>
const mockUseChatReadiness = useChatReadiness as jest.MockedFunction<
  typeof useChatReadiness
>

const chat = {
  id: "session-1",
  title: "Approval session",
  user_id: "user-1",
  entity_type: "approval",
  entity_id: "approval-1",
  tools: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
} as AgentSessionsGetSessionVercelResponse

function inboxSession(
  overrides: Partial<InboxSessionItem> = {}
): InboxSessionItem {
  return {
    id: "session-1",
    title: "Approval session",
    entity_type: "external_channel",
    entity_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    parent_workflow: null,
    created_by: null,
    derivedStatus: "PENDING_APPROVAL",
    statusLabel: "Pending approvals",
    statusPriority: 0,
    statusTone: "warning",
    pendingApprovalCount: 1,
    lastError: null,
    ...overrides,
  }
}

describe("InboxDetail", () => {
  beforeEach(() => {
    mockUseGetChatVercel.mockReturnValue({
      chat,
      chatLoading: false,
      chatFetching: false,
      chatError: null,
    })
    mockUseChatReadiness.mockReturnValue({
      ready: true,
      loading: false,
      modelInfo: { name: "gpt-4o-mini", provider: "openai" },
    })
  })

  afterEach(() => {
    jest.clearAllMocks()
  })

  it("re-arms stream resume when an approval leaves pending", () => {
    const { rerender } = render(
      <InboxDetail
        sessionId="session-1"
        parentSessionId="session-1"
        session={inboxSession()}
      />
    )

    expect(screen.getByTestId("chat-session-pane")).toHaveAttribute(
      "data-resume",
      "false"
    )

    rerender(
      <InboxDetail
        sessionId="session-1"
        parentSessionId="session-1"
        session={inboxSession({
          derivedStatus: "RUNNING",
          statusLabel: "Running",
          statusPriority: 5,
          statusTone: "info",
          pendingApprovalCount: 0,
        })}
      />
    )

    expect(screen.getByTestId("chat-session-pane")).toHaveAttribute(
      "data-resume",
      "true"
    )
  })
})

describe("InboxDetail model badge", () => {
  const sessions = new Map<string, AgentSessionsGetSessionVercelResponse>()

  beforeEach(() => {
    sessions.clear()
    mockUseGetChatVercel.mockImplementation(({ chatId }) => ({
      chat: chatId ? sessions.get(chatId) : undefined,
      chatLoading: false,
      chatFetching: false,
      chatError: null,
    }))
    // The workspace default, unless the caller passes the session's model.
    mockUseChatReadiness.mockImplementation((options) => ({
      ready: true,
      loading: false,
      modelInfo: options?.modelOverride ?? {
        name: "terra",
        provider: "openai",
      },
    }))
  })

  afterEach(() => {
    jest.clearAllMocks()
  })

  function renderBadge(sessionId: string): string | null {
    render(
      <InboxDetail
        sessionId={sessionId}
        parentSessionId="session-1"
        session={inboxSession({ pendingApprovalCount: 0 })}
      />
    )
    return screen.getByTestId("chat-session-pane").getAttribute("data-model")
  }

  it("shows the session's pinned preset model, not the workspace default", () => {
    sessions.set("session-1", {
      ...chat,
      agent_preset_id: "preset-1",
      agent_preset_version_id: "version-1",
    })

    expect(renderBadge("session-1")).toBe("claude-sonnet anthropic")
  })

  it("shows the preset's current model when the session pins no version", () => {
    sessions.set("session-1", { ...chat, agent_preset_id: "preset-1" })

    expect(renderBadge("session-1")).toBe("preset-current-model anthropic")
  })

  it("shows the source session's preset model for a follow-up fork", () => {
    // A fork has no preset of its own; its turns run on its source's.
    sessions.set("session-1", {
      ...chat,
      agent_preset_id: "preset-1",
      agent_preset_version_id: "version-1",
    })
    sessions.set("fork-1", {
      ...chat,
      id: "fork-1",
      forked_from_session_id: "session-1",
    } as AgentSessionsGetSessionVercelResponse)

    expect(renderBadge("fork-1")).toBe("claude-sonnet anthropic")
  })

  it("shows the workspace default for a session without a preset", () => {
    sessions.set("session-1", chat)

    expect(renderBadge("session-1")).toBe("terra openai")
  })
})
