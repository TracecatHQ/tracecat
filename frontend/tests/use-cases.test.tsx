/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type {
  AggregateResponse,
  CaseReadMinimal,
  CaseStatus,
  CasesSearchCasesResponse,
} from "@/client"
import { casesAggregateCases, casesSearchCases } from "@/client"
import { useCases } from "@/hooks/use-cases"

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/client", () => ({
  casesSearchCases: jest.fn(),
  casesAggregateCases: jest.fn(),
}))

const mockSearchCases = casesSearchCases as jest.MockedFunction<
  typeof casesSearchCases
>
const mockAggregateCases = casesAggregateCases as jest.MockedFunction<
  typeof casesAggregateCases
>

function createPersistedFilters(overrides: Record<string, unknown> = {}) {
  return {
    searchQuery: "",
    sortBy: { field: "updated_at", direction: "desc" },
    statusFilter: [],
    statusMode: "include",
    priorityFilter: [],
    priorityMode: "include",
    prioritySortDirection: null,
    severityFilter: [],
    severityMode: "include",
    severitySortDirection: null,
    assigneeFilter: [],
    assigneeMode: "include",
    assigneeSortDirection: null,
    tagFilter: [],
    tagMode: "include",
    tagSortDirection: null,
    dropdownFilters: {},
    updatedAfter: { type: "preset", value: null },
    createdAfter: { type: "preset", value: "1m" },
    ...overrides,
  }
}

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
    window.localStorage.clear()
  })

  afterEach(() => {
    jest.useRealTimers()
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

    const aggregate: AggregateResponse = {
      items: [
        { status: "new", count: 700 },
        { status: "in_progress", count: 200 },
        { status: "on_hold", count: 50 },
        { status: "resolved", count: 40 },
        { status: "other", count: 10 },
      ],
    }

    mockSearchCases.mockResolvedValue(firstPage)
    mockAggregateCases.mockResolvedValue(aggregate)

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

    expect(mockAggregateCases).toHaveBeenCalledTimes(1)
    expect(mockAggregateCases).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        requestBody: expect.objectContaining({
          group_by: ["status"],
          agg: [{ func: "count" }],
        }),
      })
    )
  })

  it("hydrates persisted filters before issuing case queries", async () => {
    const firstPage: CasesSearchCasesResponse = {
      items: [createCase(1)],
      next_cursor: null,
      prev_cursor: null,
      has_more: false,
      has_previous: false,
      total_estimate: 1,
    }

    const aggregate: AggregateResponse = {
      items: [{ status: "new", count: 1 }],
    }

    window.localStorage.setItem(
      "workspace-1:cases-filters:v1",
      JSON.stringify(
        createPersistedFilters({
          searchQuery: "persisted query",
          updatedAfter: { type: "preset", value: "1w" },
        })
      )
    )

    mockSearchCases.mockResolvedValue(firstPage)
    mockAggregateCases.mockResolvedValue(aggregate)

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
      expect(screen.getByTestId("rows")).toHaveTextContent("1")
    })

    expect(mockSearchCases).toHaveBeenCalledTimes(1)
    expect(mockSearchCases).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        searchTerm: "persisted query",
        updatedAfter: expect.stringContaining("T"),
      })
    )

    expect(mockAggregateCases).toHaveBeenCalledTimes(1)
    expect(mockAggregateCases).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        requestBody: expect.objectContaining({
          search: "persisted query",
        }),
      })
    )
  })

  it("does not expose cached default-query rows before persisted filters hydrate", () => {
    jest.useFakeTimers().setSystemTime(new Date("2026-03-12T12:00:00Z"))

    const cachedDefaultPage: CasesSearchCasesResponse = {
      items: [createCase(1), createCase(2)],
      next_cursor: null,
      prev_cursor: null,
      has_more: false,
      has_previous: false,
      total_estimate: 2,
    }

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    })

    const defaultFiltersKey = JSON.stringify({
      apiQueryParams: {
        startTime: new Date("2026-02-10T12:00:00.000Z").toISOString(),
      },
      hasImpossibleEnumFilter: false,
    })
    const defaultRowsKey = JSON.stringify({
      filtersKey: defaultFiltersKey,
      orderBy: "updated_at",
      sort: "desc",
    })

    queryClient.setQueryData(["cases", "workspace-1", defaultRowsKey], {
      pages: [cachedDefaultPage],
      pageParams: [null],
    })
    queryClient.setQueryData(
      ["cases", "aggregate", "workspace-1", defaultFiltersKey],
      {
        items: [{ status: "new", count: 2 }],
      } satisfies AggregateResponse
    )

    window.localStorage.setItem(
      "workspace-1:cases-filters:v1",
      JSON.stringify(
        createPersistedFilters({
          searchQuery: "persisted query",
          updatedAfter: { type: "preset", value: "1w" },
        })
      )
    )

    mockSearchCases.mockImplementation(
      () => new Promise(() => undefined) as ReturnType<typeof casesSearchCases>
    )
    mockAggregateCases.mockImplementation(
      () =>
        new Promise(() => undefined) as ReturnType<typeof casesAggregateCases>
    )

    render(
      <QueryClientProvider client={queryClient}>
        <HookProbe />
      </QueryClientProvider>
    )

    expect(screen.getByTestId("rows")).toHaveTextContent("0")
    expect(screen.getByTestId("total")).toHaveTextContent("-1")
    expect(screen.getByTestId("new-count")).toHaveTextContent("-1")
  })

  it("falls back to defaults when localStorage access throws", async () => {
    const firstPage: CasesSearchCasesResponse = {
      items: [createCase(1)],
      next_cursor: null,
      prev_cursor: null,
      has_more: false,
      has_previous: false,
      total_estimate: 1,
    }

    const aggregate: AggregateResponse = {
      items: [{ status: "new", count: 1 }],
    }

    const consoleErrorSpy = jest
      .spyOn(console, "error")
      .mockImplementation(() => undefined)
    const getItemSpy = jest
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new DOMException("Blocked", "SecurityError")
      })
    const setItemSpy = jest
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new DOMException("Blocked", "SecurityError")
      })
    const removeItemSpy = jest
      .spyOn(Storage.prototype, "removeItem")
      .mockImplementation(() => {
        throw new DOMException("Blocked", "SecurityError")
      })

    mockSearchCases.mockResolvedValue(firstPage)
    mockAggregateCases.mockResolvedValue(aggregate)

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
      expect(screen.getByTestId("rows")).toHaveTextContent("1")
    })

    expect(mockSearchCases).toHaveBeenCalledTimes(1)
    expect(mockAggregateCases).toHaveBeenCalledTimes(1)
    expect(consoleErrorSpy).toHaveBeenCalled()

    getItemSpy.mockRestore()
    setItemSpy.mockRestore()
    removeItemSpy.mockRestore()
    consoleErrorSpy.mockRestore()
  })

  it("sanitizes malformed persisted dropdown filters", async () => {
    const firstPage: CasesSearchCasesResponse = {
      items: [createCase(1)],
      next_cursor: null,
      prev_cursor: null,
      has_more: false,
      has_previous: false,
      total_estimate: 1,
    }

    const aggregate: AggregateResponse = {
      items: [{ status: "new", count: 1 }],
    }

    window.localStorage.setItem(
      "workspace-1:cases-filters:v1",
      JSON.stringify(
        createPersistedFilters({
          dropdownFilters: {
            broken: null,
            invalidMode: {
              values: ["drop-me"],
              mode: "bogus",
              sortDirection: "asc",
            },
            invalidValues: {
              values: "drop-me",
              mode: "include",
              sortDirection: "desc",
            },
            valid: {
              values: ["keep-me"],
              mode: "include",
              sortDirection: "bogus",
            },
          },
        })
      )
    )

    mockSearchCases.mockResolvedValue(firstPage)
    mockAggregateCases.mockResolvedValue(aggregate)

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
      expect(screen.getByTestId("rows")).toHaveTextContent("1")
    })

    expect(mockSearchCases).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        dropdown: ["valid:keep-me"],
      })
    )
    expect(mockAggregateCases).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        requestBody: expect.objectContaining({
          group_by: ["status"],
          agg: [{ func: "count" }],
        }),
      })
    )
  })

  it("sanitizes malformed persisted scalar filters", async () => {
    const firstPage: CasesSearchCasesResponse = {
      items: [createCase(1)],
      next_cursor: null,
      prev_cursor: null,
      has_more: false,
      has_previous: false,
      total_estimate: 1,
    }

    const aggregate: AggregateResponse = {
      items: [{ status: "new", count: 1 }],
    }

    window.localStorage.setItem(
      "workspace-1:cases-filters:v1",
      JSON.stringify(
        createPersistedFilters({
          searchQuery: 42,
          sortBy: { field: "bad-field", direction: "up" },
          statusFilter: ["new", "bad-status", 99],
          statusMode: "bogus",
          assigneeFilter: ["user-1", 123],
          tagFilter: ["tag-1", null],
        })
      )
    )

    mockSearchCases.mockResolvedValue(firstPage)
    mockAggregateCases.mockResolvedValue(aggregate)

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
      expect(screen.getByTestId("rows")).toHaveTextContent("1")
    })

    expect(mockSearchCases).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        searchTerm: undefined,
        orderBy: "updated_at",
        sort: "desc",
        status: ["new"],
        assigneeId: ["user-1"],
        tags: ["tag-1"],
      })
    )
    expect(mockAggregateCases).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        requestBody: expect.objectContaining({
          group_by: ["status"],
          agg: [{ func: "count" }],
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
    }

    const aggregate: AggregateResponse = {
      items: [{ status: "new", count: 1 }],
    }

    mockSearchCases.mockResolvedValue(firstPage)
    mockAggregateCases.mockResolvedValue(aggregate)

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
    expect(mockAggregateCases).toHaveBeenCalledTimes(1)
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

    const aggregate: AggregateResponse = {
      items: [{ status: "new", count: 1 }],
    }

    mockSearchCases.mockResolvedValue(firstPage)
    mockAggregateCases.mockResolvedValue(aggregate)

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
