/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type {
  CaseReadMinimal,
  CaseStatus,
  CasesSearchCasesResponse,
} from "@/client"
import { casesSearchCases } from "@/client"
import { useCases } from "@/hooks/use-cases"

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/client", () => ({
  casesSearchCases: jest.fn(),
}))

const mockSearchCases = casesSearchCases as jest.MockedFunction<
  typeof casesSearchCases
>

function createCase(index: number): CaseReadMinimal {
  const id = `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`
  return {
    id,
    short_id: `CASE-${String(index).padStart(4, "0")}`,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    summary: `Case ${index}`,
    status: "new",
    priority: "medium",
    severity: "low",
    assignee: null,
    tags: [],
    dropdown_values: [],
    num_tasks_completed: 0,
    num_tasks_total: 0,
  }
}

function HookProbe() {
  const { cases, totalFilteredCaseEstimate, stageCounts } = useCases({
    autoRefresh: false,
  })

  return (
    <div>
      <span data-testid="rows">{cases.length}</span>
      <span data-testid="total">{totalFilteredCaseEstimate ?? -1}</span>
      <span data-testid="new-count">{stageCounts?.new ?? -1}</span>
    </div>
  )
}

const ALL_STATUSES: CaseStatus[] = [
  "unknown",
  "new",
  "in_progress",
  "on_hold",
  "resolved",
  "closed",
  "other",
]

function ImpossibleFilterProbe() {
  const {
    cases,
    totalFilteredCaseEstimate,
    stageCounts,
    setStatusFilter,
    setStatusMode,
  } = useCases({
    autoRefresh: false,
  })

  return (
    <div>
      <span data-testid="rows">{cases.length}</span>
      <span data-testid="total">{totalFilteredCaseEstimate ?? -1}</span>
      <span data-testid="new-count">{stageCounts?.new ?? -1}</span>
      <button
        onClick={() => {
          setStatusMode("exclude")
          setStatusFilter(ALL_STATUSES)
        }}
        type="button"
      >
        impossible-filter
      </button>
    </div>
  )
}

describe("useCases", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("uses paginated rows and aggregated global counts", async () => {
    const firstPage: CasesSearchCasesResponse = {
      items: Array.from({ length: 100 }, (_, index) => createCase(index + 1)),
      next_cursor: "cursor-page-2",
      prev_cursor: null,
      has_more: true,
      has_previous: false,
      total_estimate: 1000,
      aggregation: {
        agg: "sum",
        group_by: "status",
        agg_field: null,
        value: null,
        buckets: [
          { group: "new", value: 700 },
          { group: "in_progress", value: 200 },
          { group: "on_hold", value: 50 },
          { group: "resolved", value: 40 },
          { group: "other", value: 10 },
        ],
      },
    }

    mockSearchCases.mockResolvedValue(firstPage)

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <HookProbe />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByTestId("rows")).toHaveTextContent("100")
    })

    expect(screen.getByTestId("total")).toHaveTextContent("1000")
    expect(screen.getByTestId("new-count")).toHaveTextContent("700")

    // Ensure the hook does not walk every cursor page by default.
    expect(mockSearchCases).toHaveBeenCalledTimes(1)
    expect(mockSearchCases).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        requestBody: expect.objectContaining({
          limit: 100,
          group_by: "status",
          agg: "sum",
        }),
      })
    )
  })

  it("clears rows when enum exclude filters match no records", async () => {
    const firstPage: CasesSearchCasesResponse = {
      items: [createCase(1)],
      next_cursor: null,
      prev_cursor: null,
      has_more: false,
      has_previous: false,
      total_estimate: 1,
      aggregation: {
        agg: "sum",
        group_by: "status",
        agg_field: null,
        value: null,
        buckets: [{ group: "new", value: 1 }],
      },
    }

    mockSearchCases.mockResolvedValue(firstPage)

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <ImpossibleFilterProbe />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByTestId("rows")).toHaveTextContent("1")
    })

    fireEvent.click(screen.getByRole("button", { name: "impossible-filter" }))

    await waitFor(() => {
      expect(screen.getByTestId("rows")).toHaveTextContent("0")
    })

    expect(screen.getByTestId("total")).toHaveTextContent("0")
    expect(screen.getByTestId("new-count")).toHaveTextContent("0")
    expect(mockSearchCases).toHaveBeenCalledTimes(1)
  })
})
