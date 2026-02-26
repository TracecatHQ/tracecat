/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type {
  CaseReadMinimal,
  CaseStatus,
  CasesSearchCaseAggregatesResponse,
  CasesSearchCasesResponse,
} from "@/client"
import { casesSearchCaseAggregates, casesSearchCases } from "@/client"
import { useCases } from "@/hooks/use-cases"

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/client", () => ({
  casesSearchCases: jest.fn(),
  casesSearchCaseAggregates: jest.fn(),
}))

const mockSearchCases = casesSearchCases as jest.MockedFunction<
  typeof casesSearchCases
>
const mockSearchCaseAggregates =
  casesSearchCaseAggregates as jest.MockedFunction<
    typeof casesSearchCaseAggregates
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
const DEFAULT_SORT_FIELD = "updated_at"
const DEFAULT_SORT_DIRECTION = "desc"

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

function SortStateProbe() {
  const { filters, setPrioritySortDirection, setSeveritySortDirection } =
    useCases({
      autoRefresh: false,
    })

  return (
    <div>
      <span data-testid="sort-field">{filters.sortBy.field}</span>
      <span data-testid="sort-direction">{filters.sortBy.direction}</span>
      <button onClick={() => setPrioritySortDirection("desc")} type="button">
        priority-sort
      </button>
      <button onClick={() => setPrioritySortDirection(null)} type="button">
        clear-priority-sort
      </button>
      <button onClick={() => setSeveritySortDirection("asc")} type="button">
        severity-sort
      </button>
      <button onClick={() => setSeveritySortDirection(null)} type="button">
        clear-severity-sort
      </button>
    </div>
  )
}

describe("useCases", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("uses paginated rows and separate global counts", async () => {
    const firstPage: CasesSearchCasesResponse = {
      items: Array.from({ length: 100 }, (_, index) => createCase(index + 1)),
      next_cursor: "cursor-page-2",
      prev_cursor: null,
      has_more: true,
      has_previous: false,
      total_estimate: 1000,
    }

    const aggregate: CasesSearchCaseAggregatesResponse = {
      total: 1000,
      status_groups: {
        new: 700,
        in_progress: 200,
        on_hold: 50,
        resolved: 40,
        other: 10,
      },
    }

    mockSearchCases.mockResolvedValue(firstPage)
    mockSearchCaseAggregates.mockResolvedValue(aggregate)

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
        limit: 100,
      })
    )

    expect(mockSearchCaseAggregates).toHaveBeenCalledTimes(1)
    expect(mockSearchCaseAggregates).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
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
    }

    const aggregate: CasesSearchCaseAggregatesResponse = {
      total: 1,
      status_groups: {
        new: 1,
        in_progress: 0,
        on_hold: 0,
        resolved: 0,
        other: 0,
      },
    }

    mockSearchCases.mockResolvedValue(firstPage)
    mockSearchCaseAggregates.mockResolvedValue(aggregate)

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
    expect(mockSearchCaseAggregates).toHaveBeenCalledTimes(1)
  })

  it("resets sortBy when clearing priority or severity sorting", async () => {
    const firstPage: CasesSearchCasesResponse = {
      items: [createCase(1)],
      next_cursor: null,
      prev_cursor: null,
      has_more: false,
      has_previous: false,
      total_estimate: 1,
    }

    const aggregate: CasesSearchCaseAggregatesResponse = {
      total: 1,
      status_groups: {
        new: 1,
        in_progress: 0,
        on_hold: 0,
        resolved: 0,
        other: 0,
      },
    }

    mockSearchCases.mockResolvedValue(firstPage)
    mockSearchCaseAggregates.mockResolvedValue(aggregate)

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <SortStateProbe />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByTestId("sort-field")).toHaveTextContent(
        DEFAULT_SORT_FIELD
      )
    })

    fireEvent.click(screen.getByRole("button", { name: "priority-sort" }))
    await waitFor(() => {
      expect(screen.getByTestId("sort-field")).toHaveTextContent("priority")
      expect(screen.getByTestId("sort-direction")).toHaveTextContent("desc")
    })

    fireEvent.click(screen.getByRole("button", { name: "clear-priority-sort" }))
    await waitFor(() => {
      expect(screen.getByTestId("sort-field")).toHaveTextContent(
        DEFAULT_SORT_FIELD
      )
      expect(screen.getByTestId("sort-direction")).toHaveTextContent(
        DEFAULT_SORT_DIRECTION
      )
    })

    fireEvent.click(screen.getByRole("button", { name: "severity-sort" }))
    await waitFor(() => {
      expect(screen.getByTestId("sort-field")).toHaveTextContent("severity")
      expect(screen.getByTestId("sort-direction")).toHaveTextContent("asc")
    })

    fireEvent.click(screen.getByRole("button", { name: "clear-severity-sort" }))
    await waitFor(() => {
      expect(screen.getByTestId("sort-field")).toHaveTextContent(
        DEFAULT_SORT_FIELD
      )
      expect(screen.getByTestId("sort-direction")).toHaveTextContent(
        DEFAULT_SORT_DIRECTION
      )
    })
  })
})
