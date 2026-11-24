import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { AgentSessionsListSessionsResponse } from "@/client"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"
import { TooltipProvider } from "@/components/ui/tooltip"

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceMembers: () => ({ members: [] }),
}))

jest.mock("@/components/icons", () => ({
  SlackIcon: (props: { "aria-label"?: string }) => (
    <span data-testid="slack-icon" aria-label={props["aria-label"]} />
  ),
}))

function session(id: string, title: string, overrides: object = {}) {
  return {
    id,
    workspace_id: "ws-1",
    title,
    created_by: "user-2",
    is_readonly: false,
    entity_type: "agent_preset",
    entity_id: "preset-1",
    channel_context: null,
    tools: [],
    mcp_integrations: [],
    agent_preset_id: "preset-1",
    agent_preset_version_id: null,
    harness_type: "claude_code",
    created_at: "2026-11-22T00:00:00Z",
    updated_at: "2026-11-22T00:00:00Z",
    ...overrides,
  }
}

test("marks chats linked to a Slack thread with the Slack icon", async () => {
  const chats = [
    session("linked", "Checking availability", { channel_linked: true }),
    session("teammate", "Asking for name", { is_readonly: true }),
  ] as AgentSessionsListSessionsResponse
  render(
    <TooltipProvider>
      <ChatHistoryDropdown
        chats={chats}
        isLoading={false}
        error={null}
        selectedChatId={undefined}
        onSelectChat={jest.fn()}
        workspaceId="ws-1"
        scope="team"
        onScopeChange={jest.fn()}
      />
    </TooltipProvider>
  )

  await userEvent.click(screen.getByRole("combobox"))

  const linkedRow = screen.getByText("Checking availability").parentElement
  const teammateRow = screen.getByText("Asking for name").parentElement
  expect(linkedRow).toContainElement(
    screen.getByLabelText("Linked to a Slack thread")
  )
  expect(linkedRow).not.toHaveTextContent("Read only")
  expect(teammateRow?.querySelector("[data-testid=slack-icon]")).toBeNull()
  expect(teammateRow).toHaveTextContent("Read only")
})
