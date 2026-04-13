/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
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

jest.mock("@/components/ui/tooltip", () => ({
  TooltipProvider: ({ children }: { children: ReactNode }) => children,
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => children,
  TooltipContent: ({ children }: { children: ReactNode }) => (
    <div role="tooltip">{children}</div>
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
          dropdown_values: [
            {
              id: "dropdown-value-1",
              definition_id: "dropdown-definition-1",
              definition_ref: "analyst_verdict",
              definition_name: "Analyst verdict",
              option_id: "dropdown-option-1",
              option_label: "Benign",
              option_ref: "benign",
              option_icon_name: null,
              option_color: null,
            },
          ],
          durations: [
            {
              id: "duration-value-1",
              definition_id: "duration-1",
              case_id: "case-1",
              started_at: "2026-04-13T00:00:00Z",
              ended_at: "2026-04-13T00:01:30Z",
              duration: "P1DT4H4M10.01724S",
            },
          ],
          field_values: {
            priority_reason: "Customer impact",
          },
        }}
        isSelected={false}
        onClick={jest.fn()}
        visibleColumnIds={[
          "dropdown:analyst_verdict",
          "field:priority_reason",
          "duration:duration-1",
        ]}
        fieldTypesById={new Map([["priority_reason", "TEXT"]])}
        durationNamesById={new Map([["duration-1", "Time to resolve"]])}
      />
    </QueryClientProvider>
  )
}

describe("CaseItem", () => {
  it("renders durations after tags and before timestamps, with tooltip labels", () => {
    const { container } = renderCaseItem()

    const tag = screen.getByText("prod")
    const [duration] = screen.getAllByText("Time to resolve")
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
    expect(container).toHaveTextContent("Time to resolve: 1d")
    expect(screen.getAllByText("Time to resolve")).toHaveLength(2)
    expect(
      screen.getByText("Duration: 1 day, 4 hours, 4 minutes, 10 seconds")
    ).toBeInTheDocument()
    expect(screen.getByText(/Started:/)).toBeInTheDocument()
    expect(screen.getByText(/Ended:/)).toBeInTheDocument()
    expect(screen.getByText("Analyst verdict")).toBeInTheDocument()
    expect(screen.getByText("Priority reason")).toBeInTheDocument()
  })
})
