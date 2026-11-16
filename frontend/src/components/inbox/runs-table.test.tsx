import { render, screen } from "@testing-library/react"
import {
  agentChannelBotsListBotChannels,
  type ChannelBotChannel,
  type InboxChannelThread,
  type InboxGroup,
} from "@/client"
import { RunsTable } from "@/components/inbox/runs-table"
import type { InboxGroupState } from "@/hooks/use-inbox"
import type { InboxSessionItem } from "@/lib/agents"
import { QueryClient, QueryClientProvider } from "@/lib/query"

let mockPreview = true
let mockCanReadAgents = true

jest.mock("@/client", () => ({
  agentChannelBotsListBotChannels: jest.fn(),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) =>
      key === "agent_channels_preview" && mockPreview,
    isLoading: false,
  }),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scope !== "agent:read" || mockCanReadAgents,
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-test",
}))

const ALERTS: ChannelBotChannel = {
  id: "C1",
  name: "alerts",
  is_private: false,
  is_member: true,
}
const INCIDENTS: ChannelBotChannel = {
  id: "C2",
  name: "incidents",
  is_private: true,
  is_member: true,
}

function thread(channelId: string): InboxChannelThread {
  return { bot_id: "bot-1", provider: "slack", provider_channel_id: channelId }
}

function inboxSession(
  id: string,
  channelThread: InboxChannelThread | null
): InboxSessionItem {
  return {
    id,
    title: `Session ${id}`,
    entity_type: "external_channel",
    entity_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    parent_workflow: null,
    created_by: null,
    derivedStatus: "COMPLETED",
    statusLabel: "Completed",
    statusPriority: 0,
    statusTone: "success",
    pendingApprovalCount: 0,
    lastError: null,
    channelThread,
  }
}

function group(sessions: InboxSessionItem[]): InboxGroupState {
  return {
    sessions,
    isLoading: false,
    hasMore: false,
    isLoadingMore: false,
    loadMore: () => {},
  }
}

function renderRows(sessions: InboxSessionItem[]) {
  const empty = group([])
  const groups: Record<InboxGroup, InboxGroupState> = {
    review_required: empty,
    running: empty,
    error: empty,
    completed: group(sessions),
  }
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <RunsTable
        groups={groups}
        selectedId={null}
        deletingId={null}
        onSelect={() => {}}
        orderBy="created_at"
        sort="desc"
        onSort={() => {}}
      />
    </QueryClientProvider>
  )
}

describe("RunsTable thread channel", () => {
  beforeEach(() => {
    mockPreview = true
    mockCanReadAgents = true
    jest.mocked(agentChannelBotsListBotChannels).mockReset()
    jest.mocked(agentChannelBotsListBotChannels).mockResolvedValue({
      items: [ALERTS, INCIDENTS],
      next_cursor: null,
      has_more: false,
    })
  })

  it("names each linked session's channel from one read of its bot", async () => {
    renderRows([
      inboxSession("public", thread("C1")),
      inboxSession("private", thread("C2")),
    ])

    expect(await screen.findByText("#alerts")).toBeInTheDocument()
    expect(screen.getByText("incidents")).toBeInTheDocument()
    expect(screen.getAllByLabelText("Private channel")).toHaveLength(1)
    expect(agentChannelBotsListBotChannels).toHaveBeenCalledTimes(1)
    expect(agentChannelBotsListBotChannels).toHaveBeenCalledWith({
      workspaceId: "workspace-test",
      botId: "bot-1",
      limit: 100,
    })
  })

  it("shows nothing for a channel the bot's first page does not name", async () => {
    renderRows([
      inboxSession("known", thread("C1")),
      inboxSession("unknown", thread("C9")),
    ])

    expect(await screen.findByText("#alerts")).toBeInTheDocument()
    expect(screen.getAllByLabelText("Slack")).toHaveLength(1)
    expect(screen.queryByText("C9")).not.toBeInTheDocument()
  })

  it("reads no channels for an unlinked session", () => {
    renderRows([inboxSession("unlinked", null)])

    expect(screen.getByText("Session unlinked")).toBeInTheDocument()
    expect(screen.queryByLabelText("Slack")).not.toBeInTheDocument()
    expect(agentChannelBotsListBotChannels).not.toHaveBeenCalled()
  })

  it.each([
    ["the preview entitlement", () => (mockPreview = false)],
    ["agent:read", () => (mockCanReadAgents = false)],
  ])("reads no channels without %s", (_, closeGate) => {
    closeGate()
    renderRows([inboxSession("linked", thread("C1"))])

    expect(screen.getByText("Session linked")).toBeInTheDocument()
    expect(screen.queryByLabelText("Slack")).not.toBeInTheDocument()
    expect(agentChannelBotsListBotChannels).not.toHaveBeenCalled()
  })
})
