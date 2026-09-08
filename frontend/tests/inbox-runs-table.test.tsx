import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { InboxGroup } from "@/client"
import { RunsTable } from "@/components/inbox/runs-table"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { InboxGroupState } from "@/hooks/use-inbox"
import type { InboxSessionItem } from "@/lib/agents"

const SESSION_ID = "11111111-2222-4333-8444-555555555555"

function groupState(sessions: InboxSessionItem[] = []): InboxGroupState {
  return {
    sessions,
    isLoading: false,
    hasMore: false,
    isLoadingMore: false,
    loadMore: jest.fn(),
  }
}

function sessionFixture(): InboxSessionItem {
  return {
    id: SESSION_ID,
    title: "More context needed",
    entity_type: "case",
    entity_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    created_at: "2026-07-28T14:43:00Z",
    updated_at: "2026-07-28T15:21:00Z",
    parent_workflow: null,
    created_by: null,
    derivedStatus: "COMPLETED",
    statusLabel: "Completed",
    statusPriority: 7,
    statusTone: "success",
    pendingApprovalCount: 0,
    lastError: null,
  }
}

describe("RunsTable", () => {
  it("shows and copies the agent session ID", async () => {
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    })

    const groups: Record<InboxGroup, InboxGroupState> = {
      review_required: groupState(),
      running: groupState(),
      error: groupState(),
      completed: groupState([sessionFixture()]),
    }

    render(
      <TooltipProvider>
        <RunsTable
          groups={groups}
          selectedId={null}
          deletingId={null}
          onSelect={jest.fn()}
          orderBy="created_at"
          sort="desc"
          onSort={jest.fn()}
        />
      </TooltipProvider>
    )

    expect(screen.getByText("Session ID")).toBeInTheDocument()
    expect(screen.getByTitle(SESSION_ID)).toHaveTextContent(SESSION_ID)

    fireEvent.click(screen.getByRole("button", { name: "Copy session ID" }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SESSION_ID))
  })
})
