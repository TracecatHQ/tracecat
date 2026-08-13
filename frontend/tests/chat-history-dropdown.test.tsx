import { fireEvent, render, screen } from "@testing-library/react"
import type { AgentSessionsListSessionsResponse } from "@/client"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceMembers: () => ({ members: [] }),
}))

describe("ChatHistoryDropdown", () => {
  beforeAll(() => {
    global.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  })

  it("uses selector-safe command values when chat titles contain JSON", () => {
    const onSelectChat = jest.fn()
    const unsafeTitle =
      'mcp: {"mode": "per_app_review", "client_id": "example-client-id"}'
    const chats = [
      {
        id: "chat-1",
        workspace_id: "workspace-1",
        title: unsafeTitle,
        created_by: null,
        entity_type: "agent_preset_builder",
        entity_id: "preset-1",
        channel_context: null,
        tools: null,
        mcp_integrations: null,
        agent_preset_id: null,
        agent_preset_version_id: null,
        harness_type: null,
        created_at: "2026-06-26T19:20:44Z",
        updated_at: "2026-06-26T19:20:44Z",
      },
    ] satisfies AgentSessionsListSessionsResponse

    render(
      <ChatHistoryDropdown
        chats={chats}
        isLoading={false}
        error={null}
        selectedChatId="chat-1"
        onSelectChat={onSelectChat}
        workspaceId="workspace-1"
        scope="team"
        onScopeChange={jest.fn()}
      />
    )

    fireEvent.click(screen.getByText("Chats"))

    expect(screen.getByText(unsafeTitle)).toBeInTheDocument()
    expect(() => {
      document.querySelector(
        `[cmdk-item=""][data-value="${unsafeTitle} chat-1"]`
      )
    }).toThrow()
    expect(() => {
      document.querySelector('[cmdk-item=""][data-value="chat-1"]')
    }).not.toThrow()

    fireEvent.click(screen.getByText(unsafeTitle))

    expect(onSelectChat).toHaveBeenCalledWith("chat-1")
  })

  it("badges comment invocation sessions and makes their origin searchable", () => {
    const onSelectChat = jest.fn()
    const chats = [
      {
        id: "comment-session",
        workspace_id: "workspace-1",
        title: "Renamed investigation",
        created_by: "user-1",
        entity_type: "case",
        entity_id: "case-1",
        channel_context: { session_origin: "case_comment" },
        tools: null,
        mcp_integrations: null,
        agent_preset_id: null,
        agent_preset_version_id: "preset-version-1",
        harness_type: null,
        created_at: "2026-06-26T19:20:44Z",
        updated_at: "2026-06-26T19:20:44Z",
      },
      {
        id: "regular-session",
        workspace_id: "workspace-1",
        title: "Investigate case comment",
        created_by: "user-1",
        entity_type: "case",
        entity_id: "case-1",
        channel_context: null,
        tools: null,
        mcp_integrations: null,
        agent_preset_id: "preset-1",
        agent_preset_version_id: "preset-version-1",
        harness_type: null,
        created_at: "2026-06-26T19:19:44Z",
        updated_at: "2026-06-26T19:19:44Z",
      },
      {
        id: "similar-session",
        workspace_id: "workspace-1",
        title: "case comment follow-up",
        created_by: "user-1",
        entity_type: "case",
        entity_id: "case-1",
        channel_context: null,
        tools: null,
        mcp_integrations: null,
        agent_preset_id: "preset-1",
        agent_preset_version_id: "preset-version-1",
        harness_type: null,
        created_at: "2026-06-26T19:18:44Z",
        updated_at: "2026-06-26T19:18:44Z",
      },
    ] satisfies AgentSessionsListSessionsResponse

    render(
      <ChatHistoryDropdown
        chats={chats}
        isLoading={false}
        error={null}
        selectedChatId={undefined}
        onSelectChat={onSelectChat}
        workspaceId="workspace-1"
        scope="team"
        onScopeChange={jest.fn()}
      />
    )

    fireEvent.click(screen.getByText("Chats"))

    expect(screen.getByText("Renamed investigation")).toBeInTheDocument()
    expect(screen.getAllByText("From comment")).toHaveLength(1)
    expect(screen.getByText("Investigate case comment")).toBeInTheDocument()
    expect(screen.getByText("case comment follow-up")).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText("Search chats..."), {
      target: { value: "from comment" },
    })

    expect(screen.getByText("Renamed investigation")).toBeInTheDocument()
    expect(
      screen.queryByText("Investigate case comment")
    ).not.toBeInTheDocument()
    expect(screen.queryByText("case comment follow-up")).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("Renamed investigation"))
    expect(onSelectChat).toHaveBeenCalledWith("comment-session")
  })
})
