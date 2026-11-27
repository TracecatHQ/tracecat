import { render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  type TriggerRead,
  triggersGetTriggerRunOrigin,
  triggersListTriggerRuns,
  triggersListTriggerStoppedEvents,
} from "@/client"
import {
  StartedByTrigger,
  TriggerRunsSections,
} from "@/components/triggers/trigger-runs"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  triggersGetTriggerRunOrigin: jest.fn(),
  triggersListTriggerRuns: jest.fn(),
  triggersListTriggerStoppedEvents: jest.fn(),
}))

const TRIGGER: TriggerRead = {
  id: "trigger-1",
  kind: "object_storage",
  name: "New uploads",
  status: "online",
  config: {
    kind: "object_storage",
    source_id: "source-1",
    source_name: "Uploads",
    key_prefix: "",
  },
  bindings: [],
  created_at: "2026-11-27T00:00:00Z",
  updated_at: "2026-11-27T00:00:00Z",
}

function renderWithClient(ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  })
}

beforeEach(() => {
  jest.clearAllMocks()
  jest
    .mocked(triggersListTriggerStoppedEvents)
    .mockResolvedValue({ items: [], has_more: false })
})

describe("TriggerRunsSections", () => {
  it("links workflow runs to the run page and agent runs to the session", async () => {
    jest.mocked(triggersListTriggerRuns).mockResolvedValue({
      items: [
        {
          target_type: "workflow",
          target_name: "Scan file",
          wf_exec_id: "wf_abc/exec_def",
          workflow_id: "wf_abc",
          status: "COMPLETED",
          start_time: "2026-11-27T00:00:00Z",
        },
        {
          target_type: "agent",
          target_name: "New uploads",
          wf_exec_id: "agent-turn-1",
          session_id: "session-1",
          agent_preset_id: "preset-1",
          status: "RUNNING",
          start_time: "2026-11-27T00:00:00Z",
        },
      ],
      has_more: true,
      next_cursor: "next",
    })

    renderWithClient(
      <TriggerRunsSections workspaceId="workspace-1" trigger={TRIGGER} />
    )

    expect(
      await screen.findByRole("link", { name: "Scan file" })
    ).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/workflows/wf_abc/executions/exec_def"
    )
    expect(screen.getByRole("link", { name: "New uploads" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/agents/preset-1?tab=live-chat&session=session-1"
    )
    expect(screen.getByRole("button", { name: "Load more" })).toBeEnabled()
    expect(await screen.findByText("No stopped events.")).toBeInTheDocument()
  })

  it("shows stopped events with their error", async () => {
    jest
      .mocked(triggersListTriggerRuns)
      .mockResolvedValue({ items: [], has_more: false })
    jest.mocked(triggersListTriggerStoppedEvents).mockResolvedValue({
      items: [
        {
          id: 7,
          attempts: 8,
          error: "binding b: RPCError: unavailable",
          created_at: "2026-11-27T00:00:00Z",
        },
      ],
      has_more: false,
    })

    renderWithClient(
      <TriggerRunsSections workspaceId="workspace-1" trigger={TRIGGER} />
    )

    expect(await screen.findByText("Event 7")).toBeInTheDocument()
    expect(
      screen.getByText("binding b: RPCError: unavailable")
    ).toBeInTheDocument()
    expect(screen.getByText(/No runs yet/)).toBeInTheDocument()
  })

  it("explains a missing run read scope and skips stopped events for other kinds", async () => {
    jest
      .mocked(triggersListTriggerRuns)
      .mockRejectedValue(Object.assign(new Error("Forbidden"), { status: 403 }))

    renderWithClient(
      <TriggerRunsSections
        workspaceId="workspace-1"
        trigger={{ ...TRIGGER, kind: "webhook" }}
      />
    )

    expect(
      await screen.findByText(/You need permission to view workflow runs/)
    ).toBeInTheDocument()
    expect(screen.queryByText("Stopped events")).not.toBeInTheDocument()
    expect(triggersListTriggerStoppedEvents).not.toHaveBeenCalled()
  })
})

it("keeps Load more on an empty page that has more after it", async () => {
  // Child runs are filtered out of a page, so one can come back empty.
  jest
    .mocked(triggersListTriggerRuns)
    .mockResolvedValue({ items: [], has_more: true, next_cursor: "next" })

  renderWithClient(
    <TriggerRunsSections workspaceId="workspace-1" trigger={TRIGGER} />
  )

  expect(await screen.findByRole("button", { name: "Load more" })).toBeEnabled()
  expect(screen.queryByText(/No runs yet/)).not.toBeInTheDocument()
})

describe("StartedByTrigger", () => {
  it("links back to the trigger that started the run", async () => {
    jest.mocked(triggersGetTriggerRunOrigin).mockResolvedValue({
      trigger_id: "trigger-1",
      name: "New uploads",
      kind: "object_storage",
    })

    renderWithClient(
      <StartedByTrigger
        workspaceId="workspace-1"
        target={{ wfExecId: "wf_abc/exec_def" }}
      />
    )

    expect(
      await screen.findByRole("link", { name: "New uploads" })
    ).toHaveAttribute("href", "/workspaces/workspace-1/triggers/trigger-1")
    expect(triggersGetTriggerRunOrigin).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      wfExecId: "wf_abc/exec_def",
      sessionId: undefined,
    })
  })

  it("renders nothing for a run no trigger started", async () => {
    jest.mocked(triggersGetTriggerRunOrigin).mockResolvedValue(null)

    const { container } = renderWithClient(
      <StartedByTrigger workspaceId="workspace-1" target={{ sessionId: "s" }} />
    )

    await waitFor(() => expect(triggersGetTriggerRunOrigin).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
