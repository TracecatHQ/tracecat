/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { CaseItem } from "@/components/cases/case-item"

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/components/cases/cases-feed-event", () => ({
  EventCreatedAt: ({ createdAt }: { createdAt: string }) => (
    <span>{`created ${createdAt}`}</span>
  ),
  EventUpdatedAt: ({ updatedAt }: { updatedAt: string }) => (
    <span>{`updated ${updatedAt}`}</span>
  ),
}))

function renderCaseItem() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <CaseItem
        caseData={{
          id: "case-1",
          short_id: "CASE-0001",
          created_at: "2026-04-13T00:00:00Z",
          updated_at: "2026-04-13T00:01:30Z",
          summary: "Database alert",
          status: "new",
          priority: "medium",
          severity: "high",
          tags: [{ id: "tag-1", ref: "prod", name: "prod", color: null }],
          dropdown_values: [],
          durations: [
            {
              id: "duration-value-1",
              definition_id: "duration-1",
              case_id: "case-1",
              started_at: "2026-04-13T00:00:00Z",
              ended_at: "2026-04-13T00:01:30Z",
              duration: "PT1M30S",
            },
          ],
          field_values: {
            priority_reason: "Customer impact",
          },
        }}
        isSelected={false}
        onClick={jest.fn()}
        visibleColumnIds={["field:priority_reason", "duration:duration-1"]}
        fieldTypesById={new Map([["priority_reason", "TEXT"]])}
        durationNamesById={new Map([["duration-1", "Time to resolve"]])}
      />
    </QueryClientProvider>
  )
}

describe("CaseItem", () => {
  it("renders durations after tags and before timestamps, with hover labels", async () => {
    const { container } = renderCaseItem()

    const tag = screen.getByText("prod")
    const duration = screen.getByText("Time to resolve")
    const createdAt = screen.getByText("created 2026-04-13T00:00:00Z")
    const customFieldValue = screen.getByText("Customer impact")
    const customFieldBadge = customFieldValue.closest("div.inline-flex")
    const durationBadge = duration.closest("div.inline-flex")

    expect(customFieldBadge).not.toBeNull()
    expect(durationBadge).not.toBeNull()

    expect(
      tag.compareDocumentPosition(duration) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      duration.compareDocumentPosition(createdAt) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(container).toHaveTextContent("Time to resolve: 1m30s")
    expect(screen.getAllByText("Time to resolve")).toHaveLength(1)

    expect(customFieldBadge).toHaveAttribute("title", "priority_reason")
    expect(durationBadge).toHaveAttribute("title", "Time to resolve")
  })
})
