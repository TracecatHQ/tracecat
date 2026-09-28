import { render, screen } from "@testing-library/react"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { InboxDetail } from "@/components/inbox/inbox-detail"
import { useGetChatVercel } from "@/hooks/use-chat"
import type { InboxSessionItem } from "@/lib/agents"
import { useChatReadiness } from "@/lib/hooks"

jest.mock("@/components/chat/chat-session-pane", () => ({
  ChatSessionPane: ({ resume }: { resume?: boolean }) => (
    <div data-testid="chat-session-pane" data-resume={String(resume)} />
  ),
}))
jest.mock("@/hooks/use-chat", () => ({
  useGetChatVercel: jest.fn(),
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
