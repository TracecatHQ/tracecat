import { fireEvent, render, screen } from "@testing-library/react"
import type { AgentSessionsListSessionsResponse } from "@/client"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"

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
})
