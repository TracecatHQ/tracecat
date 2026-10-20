import { act, renderHook } from "@testing-library/react"
import axios from "axios"
import type { ReactNode } from "react"
import {
  type CaseReadMinimal,
  type CasesSearchCaseAggregatesResponse,
  type CasesSearchCasesResponse,
  casesQueryCaseAggregates,
  casesQueryCases,
} from "@/client"
import { CancelablePromise } from "@/client/core/CancelablePromise"
import { useCases } from "@/hooks/use-cases"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

jest.mock("@/client", () => ({
  casesQueryCases: jest.fn(),
  casesQueryCaseAggregates: jest.fn(),
}))

const searchCases = jest.mocked(casesQueryCases)
const searchAggregates = jest.mocked(casesQueryCaseAggregates)

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
const OTHER_WORKSPACE_ID = "22222222-2222-4222-8222-222222222222"

const CASE: CaseReadMinimal = {
  id: "00000000-0000-4000-8000-000000000042",
  short_id: "CASE-0042",
  created_at: "2020-01-01T00:00:00Z",
  updated_at: "2020-01-01T00:00:00Z",
  summary: "Synthetic case",
  status: "new",
  priority: "low",
  severity: "low",
  dropdown_values: [],
  num_sub_cases: 0,
}

function setup(
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
) {
  let workspaceId = WORKSPACE_ID
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <WorkspaceIdProvider workspaceId={workspaceId}>
        {children}
      </WorkspaceIdProvider>
    </QueryClientProvider>
  )
  const hook = renderHook(() => useCases({ autoRefresh: false }), { wrapper })
  return {
    ...hook,
    client,
    changeWorkspace(nextWorkspaceId: string) {
      workspaceId = nextWorkspaceId
      hook.rerender()
    },
  }
}

async function advance(ms = 1) {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(ms)
  })
}

function searchTerms(mock: jest.Mock) {
  return mock.mock.calls.map(([params]) => params.requestBody.search_term)
}

function resolved<T>(value: T) {
  return new CancelablePromise<T>((resolve) => resolve(value))
}

beforeEach(() => {
  jest.useFakeTimers()
  jest.clearAllMocks()
  window.localStorage.clear()
  searchCases.mockImplementation(() =>
    resolved<CasesSearchCasesResponse>({ items: [], has_more: false })
  )
  searchAggregates.mockImplementation(() =>
    resolved<CasesSearchCaseAggregatesResponse>({ total: 0, status_groups: {} })
  )
})

afterEach(() => {
  jest.restoreAllMocks()
  jest.useRealTimers()
})

it("debounces search requests while typing", async () => {
  const sdk = jest.requireActual<typeof import("@/client")>("@/client")
  searchCases.mockImplementation(sdk.casesQueryCases)
  searchAggregates.mockImplementation(sdk.casesQueryCaseAggregates)
  const transport = jest
    .spyOn(axios, "request")
    .mockImplementation(() => new Promise(() => undefined))
  const { result, unmount } = setup()
  await advance(1)
  expect(searchTerms(searchCases)).toEqual([undefined])

  for (const query of ["p", "po", "pow"]) {
    act(() => result.current.setSearchQuery(query))
    await advance(100)
  }
  act(() => result.current.setSearchQuery("powershell"))
  expect(result.current.filters.searchQuery).toBe("powershell")
  expect(result.current.appliedSearchQuery).toBe("")
  expect(result.current.isSearchPending).toBe(true)
  await advance(299)
  expect(searchTerms(searchCases)).toEqual([undefined])
  expect(searchTerms(searchAggregates)).toEqual([undefined])

  await advance(1)
  expect(searchTerms(searchCases)).toEqual([undefined, "powershell"])
  expect(searchTerms(searchAggregates)).toEqual([undefined, "powershell"])
  expect(result.current.appliedSearchQuery).toBe("powershell")
  expect(result.current.isSearchPending).toBe(false)

  const supersededSignals = transport.mock.calls
    .slice(-2)
    .map(([config]) => config.signal)
  expect(supersededSignals.map((signal) => signal?.aborted)).toEqual([
    false,
    false,
  ])
  act(() => result.current.setSearchQuery(CASE.short_id))
  await advance(299)
  expect(result.current.appliedSearchQuery).toBe("powershell")
  expect(supersededSignals.map((signal) => signal?.aborted)).toEqual([
    false,
    false,
  ])
  await advance(1)
  expect(supersededSignals.map((signal) => signal?.aborted)).toEqual([
    true,
    true,
  ])
  expect(searchCases).toHaveBeenLastCalledWith(
    expect.objectContaining({
      requestBody: expect.objectContaining({
        search_term: CASE.short_id,
        promote_exact_match: true,
      }),
    })
  )
  expect(searchAggregates).toHaveBeenLastCalledWith(
    expect.objectContaining({
      requestBody: expect.objectContaining({ search_term: CASE.short_id }),
    })
  )
  const activeSignals = transport.mock.calls
    .slice(-2)
    .map(([config]) => config.signal)
  act(() => result.current.setSearchQuery("pending text"))
  unmount()
  expect(activeSignals.map((signal) => signal?.aborted)).toEqual([true, true])
  await advance(500)
  expect(searchTerms(searchCases)).toEqual([
    undefined,
    "powershell",
    CASE.short_id,
  ])
})

it("applies a cleared search immediately", async () => {
  searchCases.mockImplementation((request) =>
    resolved<CasesSearchCasesResponse>({
      items: [
        {
          ...CASE,
          id: request.requestBody.cursor
            ? "00000000-0000-4000-8000-000000000043"
            : CASE.id,
        },
      ],
      has_more: !request.requestBody.cursor,
      next_cursor: request.requestBody.cursor ? null : "synthetic-cursor",
    })
  )
  const { result, client, unmount } = setup()
  await advance(1)
  act(() => result.current.setCreatedAfter({ type: "preset", value: null }))
  await advance(1)
  act(() => result.current.fetchNextPage())
  await advance(1)
  expect(result.current.cases).toHaveLength(2)
  const rowRequests = searchCases.mock.calls.length
  const countRequests = searchAggregates.mock.calls.length
  act(() => result.current.setSearchQuery(""))
  await advance(300)
  expect(result.current.cases).toHaveLength(2)
  expect(searchCases).toHaveBeenCalledTimes(rowRequests)
  expect(searchAggregates).toHaveBeenCalledTimes(countRequests)

  act(() => result.current.setSearchQuery("phishing"))
  await advance(300)
  await advance(1)
  act(() => result.current.fetchNextPage())
  await advance(1)
  expect(result.current.cases).toHaveLength(2)
  act(() => result.current.setSearchQuery("pending text"))
  await advance(100)
  searchCases.mockClear()
  act(() => result.current.setSearchQuery(""))
  expect(result.current.appliedSearchQuery).toBe("")
  expect(result.current.isSearchPending).toBe(false)
  await advance(1)
  expect(result.current.cases).toHaveLength(1)
  expect(searchCases).toHaveBeenLastCalledWith(
    expect.objectContaining({
      requestBody: expect.objectContaining({
        search_term: undefined,
        cursor: undefined,
      }),
    })
  )
  await advance(500)
  expect(searchTerms(searchCases)).toEqual([undefined])

  unmount()
  const remounted = setup(client)
  await advance(1)
  act(() => remounted.result.current.setSearchQuery("phishing"))
  await advance(300)
  await advance(1)
  expect(remounted.result.current.cases).toHaveLength(1)
  expect(searchCases).toHaveBeenLastCalledWith(
    expect.objectContaining({
      requestBody: expect.objectContaining({
        search_term: "phishing",
        cursor: undefined,
      }),
    })
  )
})

describe("case search aggregate counts", () => {
  it("opts out of row counts on initial and continuation pages and uses only aggregate totals", async () => {
    let resolveCounts!: (value: CasesSearchCaseAggregatesResponse) => void
    const counts = new CancelablePromise<CasesSearchCaseAggregatesResponse>(
      (resolve) => {
        resolveCounts = resolve
      }
    )
    jest.mocked(casesQueryCaseAggregates).mockImplementation(() => counts)
    jest.mocked(casesQueryCases).mockImplementation((request) =>
      resolved<CasesSearchCasesResponse>({
        items: [
          {
            ...CASE,
            id: request.requestBody.cursor
              ? "00000000-0000-4000-8000-000000000043"
              : CASE.id,
          },
        ],
        has_more: !request.requestBody.cursor,
        next_cursor: request.requestBody.cursor
          ? null
          : "synthetic-continuation",
        // A cached legacy page total must not become another count source.
        total_estimate: request.requestBody.cursor ? null : 999,
      })
    )
    const { result } = setup()
    await advance()
    expect(casesQueryCases).toHaveBeenCalledTimes(1)
    expect(casesQueryCaseAggregates).toHaveBeenCalledTimes(1)
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          include_count: false,
          cursor: undefined,
        }),
      })
    )
    expect(result.current.totalFilteredCaseEstimate).toBeNull()
    act(() =>
      resolveCounts({
        total: 12,
        status_groups: { new: 12 },
      })
    )
    await advance()
    expect(result.current.totalFilteredCaseEstimate).toBe(12)
    expect(result.current.stageCounts?.new).toBe(12)
    act(() => result.current.fetchNextPage())
    await advance()
    expect(result.current.cases).toHaveLength(2)
    expect(casesQueryCases).toHaveBeenCalledTimes(2)
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          include_count: false,
          cursor: "synthetic-continuation",
        }),
      })
    )
    expect(casesQueryCaseAggregates).toHaveBeenCalledTimes(1)
    expect(result.current.totalFilteredCaseEstimate).toBe(12)
    expect(result.current.stageCounts?.new).toBe(12)
  })

  it("keeps successful rows when population counts exhaust retries", async () => {
    jest.mocked(casesQueryCases).mockImplementation(() =>
      resolved<CasesSearchCasesResponse>({
        items: [CASE],
        has_more: true,
        next_cursor: "synthetic-next-page",
        total_estimate: null,
      })
    )
    jest.mocked(casesQueryCaseAggregates).mockImplementation(
      () =>
        new CancelablePromise((_resolve, reject) =>
          reject(
            Object.assign(new Error("Synthetic count failure"), {
              status: 500,
            })
          )
        )
    )
    const { result } = setup()
    await advance(8000)
    expect(casesQueryCaseAggregates).toHaveBeenCalledTimes(4)
    expect(result.current).toMatchObject({
      cases: [CASE],
      error: null,
      isLoading: false,
      totalFilteredCaseEstimate: null,
      stageCounts: null,
      isCountsLoading: false,
      isCountsFetching: false,
      isCountsError: true,
    })
    act(() => result.current.setSearchQuery(CASE.short_id))
    await advance(300)
    expect(result.current).toMatchObject({ isCountsError: false })
  })

  it("discards stale totals after a successful count refresh fails", async () => {
    jest.mocked(casesQueryCaseAggregates).mockImplementation(() =>
      resolved<CasesSearchCaseAggregatesResponse>({
        total: 12,
        status_groups: { new: 12 },
      })
    )
    const { result } = setup()
    await advance()
    expect(result.current.totalFilteredCaseEstimate).toBe(12)
    jest.mocked(casesQueryCaseAggregates).mockImplementation(
      () =>
        new CancelablePromise((_resolve, reject) =>
          reject(
            Object.assign(new Error("Synthetic count refresh failure"), {
              status: 500,
            })
          )
        )
    )
    act(() => result.current.refetch())
    await advance(8000)
    expect(result.current.isCountsError).toBe(true)
    expect(result.current.totalFilteredCaseEstimate).toBeNull()
    expect(result.current.stageCounts).toBeNull()
  })
})

describe("case search ordering", () => {
  it.each(["assignee", "tags"] as const)(
    "requests global %s order and preserves it across loaded pages",
    async (field) => {
      const first: CaseReadMinimal = {
        ...CASE,
        assignee: {
          id: "00000000-0000-4000-8000-000000000001",
          email: "zulu@sorting.example.com",
          role: "basic",
          settings: {},
        },
        tags: [
          {
            id: "synthetic-tag-zulu",
            name: "Zulu",
            ref: "synthetic-zulu",
            color: null,
          },
          {
            id: "synthetic-tag-yankee",
            name: "Yankee",
            ref: "synthetic-yankee",
            color: null,
          },
        ],
      }
      const second: CaseReadMinimal = {
        ...CASE,
        id: "00000000-0000-4000-8000-000000000043",
        assignee: {
          id: "00000000-0000-4000-8000-000000000002",
          email: "alpha@sorting.example.com",
          role: "basic",
          settings: {},
        },
        tags: [
          {
            id: "synthetic-tag-bravo",
            name: "Bravo",
            ref: "synthetic-bravo",
            color: null,
          },
        ],
      }
      jest.mocked(casesQueryCases).mockImplementation((request) =>
        resolved<CasesSearchCasesResponse>({
          items: request.requestBody.cursor ? [second] : [first],
          has_more: !request.requestBody.cursor,
          next_cursor: request.requestBody.cursor
            ? null
            : "synthetic-sort-cursor",
          total_estimate: null,
        })
      )
      const { result } = setup()
      await advance()
      act(() => result.current.setSortBy({ field, direction: "asc" }))
      await advance()
      expect(result.current.cases).toEqual([first])
      expect(casesQueryCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          requestBody: expect.objectContaining({
            order_by: field,
            sort: "asc",
            include_count: false,
            cursor: undefined,
          }),
        })
      )
      act(() => result.current.fetchNextPage())
      await advance()
      expect(result.current.cases).toEqual([first, second])
      expect(casesQueryCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          requestBody: expect.objectContaining({
            order_by: field,
            sort: "asc",
            include_count: false,
            cursor: "synthetic-sort-cursor",
          }),
        })
      )
    }
  )

  it("keeps a single active sort when switching fields and clearing", async () => {
    const { result } = setup()
    await advance()
    act(() => result.current.setPrioritySortDirection("asc"))
    act(() => result.current.setAssigneeSortDirection("desc"))
    expect(result.current.filters.sortBy).toEqual({
      field: "assignee",
      direction: "desc",
    })
    expect(result.current.filters.prioritySortDirection).toBeNull()
    expect(result.current.filters.assigneeSortDirection).toBe("desc")
    act(() => result.current.setTagSortDirection("asc"))
    expect(result.current.filters.sortBy).toEqual({
      field: "tags",
      direction: "asc",
    })
    expect(result.current.filters.assigneeSortDirection).toBeNull()
    expect(result.current.filters.tagSortDirection).toBe("asc")
    act(() => result.current.setSeveritySortDirection("desc"))
    expect(result.current.filters.tagSortDirection).toBeNull()
    expect(result.current.filters.sortBy).toEqual({
      field: "severity",
      direction: "desc",
    })
    act(() => result.current.setSeveritySortDirection(null))
    expect(result.current.filters.sortBy).toEqual({
      field: "updated_at",
      direction: "desc",
    })
    expect(result.current.filters.prioritySortDirection).toBeNull()
    expect(result.current.filters.severitySortDirection).toBeNull()
    expect(result.current.filters.assigneeSortDirection).toBeNull()
    expect(result.current.filters.tagSortDirection).toBeNull()
  })

  it("normalizes competing persisted flags to the previous effective order", async () => {
    window.localStorage.setItem(
      `${WORKSPACE_ID}:cases-filters:v1`,
      JSON.stringify({
        sortBy: { field: "priority", direction: "desc" },
        prioritySortDirection: "desc",
        severitySortDirection: "asc",
        assigneeSortDirection: "asc",
        tagSortDirection: "desc",
      })
    )
    const { result, changeWorkspace } = setup()
    await advance()
    expect(result.current.filters.sortBy).toEqual({
      field: "assignee",
      direction: "asc",
    })
    expect(result.current.filters.assigneeSortDirection).toBe("asc")
    expect(result.current.filters.tagSortDirection).toBeNull()
    expect(result.current.filters.prioritySortDirection).toBeNull()
    expect(result.current.filters.severitySortDirection).toBeNull()
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          order_by: "assignee",
          sort: "asc",
        }),
      })
    )
    window.localStorage.setItem(
      `${OTHER_WORKSPACE_ID}:cases-filters:v1`,
      JSON.stringify({
        searchQuery: "powershell",
        searchOrder: "relevance",
        sortBy: { field: "assignee", direction: "asc" },
        assigneeSortDirection: "asc",
      })
    )
    act(() => changeWorkspace(OTHER_WORKSPACE_ID))
    await advance()
    expect(result.current.filters.sortBy).toEqual({
      field: "relevance",
      direction: "desc",
    })
    expect(result.current.filters.prioritySortDirection).toBeNull()
    expect(result.current.filters.severitySortDirection).toBeNull()
    expect(result.current.filters.assigneeSortDirection).toBeNull()
    expect(result.current.filters.tagSortDirection).toBeNull()
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          order_by: "relevance",
          sort: "desc",
        }),
      })
    )
    expect(
      JSON.parse(
        window.localStorage.getItem(`${OTHER_WORKSPACE_ID}:cases-filters:v1`) ??
          "{}"
      )
    ).toMatchObject({
      sortBy: { field: "assignee", direction: "asc" },
      assigneeSortDirection: "asc",
    })
    act(() => result.current.setSearchMode("literal"))
    await advance()
    expect(result.current.filters.assigneeSortDirection).toBe("asc")
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          order_by: "assignee",
          sort: "asc",
        }),
      })
    )
  })
})

describe("case search modes", () => {
  it("applies lexical mode to rows/counts together and resets the cursor when mode changes", async () => {
    jest.mocked(casesQueryCases).mockImplementation(() =>
      resolved<CasesSearchCasesResponse>({
        items: [CASE],
        has_more: true,
        next_cursor: "synthetic-mode-cursor",
      })
    )
    const { result } = setup()
    await advance()
    act(() => result.current.setSearchQuery("powershell encoded"))
    await advance(301)
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: "powershell encoded",
          search_mode: "full_text",
          order_by: "relevance",
          sort: "desc",
          cursor: undefined,
          include_count: false,
        }),
      })
    )
    expect(casesQueryCaseAggregates).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: "powershell encoded",
          search_mode: "full_text",
        }),
      })
    )
    await advance(10)
    expect(result.current.hasNextPage).toBe(true)
    act(() => result.current.fetchNextPage())
    await advance()
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          cursor: "synthetic-mode-cursor",
        }),
      })
    )
    act(() => result.current.setSearchMode("literal"))
    await advance()
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_mode: "literal",
          order_by: "updated_at",
          cursor: undefined,
        }),
      })
    )
    expect(casesQueryCaseAggregates).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({ search_mode: "literal" }),
      })
    )
    expect(result.current.filters.sortBy.field).toBe("updated_at")
  })

  it("preserves literal edge spaces and switches modes without applying pending input", async () => {
    const { result } = setup()
    await advance()
    act(() => {
      result.current.setSearchMode("literal")
      result.current.setSearchQuery(" foo ")
    })
    await advance(301)
    expect(result.current.appliedSearchQuery).toBe(" foo ")
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: " foo ",
          search_mode: "literal",
        }),
      })
    )
    expect(casesQueryCaseAggregates).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: " foo ",
          search_mode: "literal",
        }),
      })
    )
    act(() => result.current.setSearchQuery(" bar "))
    await advance(100)
    act(() => result.current.setSearchMode("full_text"))
    await advance()
    expect(result.current.appliedSearchQuery).toBe("foo")
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: "foo",
          search_mode: "full_text",
        }),
      })
    )
    expect(casesQueryCaseAggregates).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: "foo",
          search_mode: "full_text",
        }),
      })
    )
    await advance(301)
    expect(result.current.appliedSearchQuery).toBe("bar")
    act(() => result.current.setSearchMode("literal"))
    await advance()
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: " bar ",
          search_mode: "literal",
        }),
      })
    )
    expect(casesQueryCaseAggregates).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: " bar ",
          search_mode: "literal",
        }),
      })
    )
    act(() => result.current.setSearchQuery("   "))
    await advance()
    expect(result.current.appliedSearchQuery).toBe("")
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: undefined,
          search_mode: undefined,
        }),
      })
    )
  })

  it("keeps an explicit default-column choice across terms and offers relevance again", async () => {
    const { result } = setup()
    await advance()
    act(() => {
      result.current.setSearchQuery("powershell")
      result.current.setSortBy({ field: "updated_at", direction: "desc" })
    })
    await advance(301)
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_mode: "full_text",
          order_by: "updated_at",
        }),
      })
    )
    act(() => result.current.setSearchQuery("encoded"))
    await advance(301)
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          order_by: "updated_at",
          search_term: "encoded",
        }),
      })
    )
    act(() => result.current.setPrioritySortDirection("asc"))
    expect(result.current.filters.prioritySortDirection).toBe("asc")
    act(() =>
      result.current.setSortBy({ field: "relevance", direction: "desc" })
    )
    await advance()
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({ order_by: "relevance" }),
      })
    )
    expect(result.current.filters.prioritySortDirection).toBeNull()
    act(() => result.current.setSearchQuery(""))
    await advance()
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_mode: undefined,
          order_by: "updated_at",
        }),
      })
    )
  })

  it("keeps literal content matching and server-owned reference promotion together", async () => {
    const { result } = setup()
    await advance()
    act(() => {
      result.current.setSearchMode("literal")
      result.current.setSearchQuery("CASE-0042")
    })
    await advance(301)
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_mode: "literal",
          search_term: "CASE-0042",
          promote_exact_match: true,
          start_time: expect.any(String),
          hierarchy: "all",
        }),
      })
    )
    expect(casesQueryCaseAggregates).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_mode: "literal",
          search_term: "CASE-0042",
        }),
      })
    )
    const stored = JSON.parse(
      window.localStorage.getItem(`${WORKSPACE_ID}:cases-filters:v1`) ?? "{}"
    )
    expect(stored.searchMode).toBe("literal")
  })
})

it("shares structured constraints with counts, resets cursors, and persists removed fields", async () => {
  searchCases.mockImplementation((request) =>
    resolved<CasesSearchCasesResponse>({
      items: [
        {
          ...CASE,
          id: request.requestBody.cursor
            ? "00000000-0000-4000-8000-000000000043"
            : CASE.id,
        },
      ],
      has_more: !request.requestBody.cursor,
      next_cursor: request.requestBody.cursor ? null : "structured-cursor",
    })
  )
  const tree = {
    and: [
      { field: "fields.deleted_field", op: "eq" as const, value: "synthetic" },
      {
        field: "payload.metadata.product.vendor_name",
        op: "eq" as const,
        value: "Microsoft",
      },
    ],
  }
  const { result } = setup()
  await advance()
  act(() => result.current.setStructuredFilters(tree))
  await advance()
  expect(searchCases).toHaveBeenLastCalledWith(
    expect.objectContaining({
      requestBody: expect.objectContaining({
        filters: tree,
        cursor: undefined,
      }),
    })
  )
  expect(searchAggregates).toHaveBeenLastCalledWith(
    expect.objectContaining({
      requestBody: expect.objectContaining({ filters: tree }),
    })
  )
  act(() => result.current.fetchNextPage())
  await advance()
  expect(result.current.cases).toHaveLength(2)
  act(() => result.current.setStructuredFilters(null))
  await advance()
  act(() => result.current.setStructuredFilters(tree))
  await advance()
  expect(result.current.cases).toHaveLength(1)
  expect(searchCases).toHaveBeenLastCalledWith(
    expect.objectContaining({
      requestBody: expect.objectContaining({
        filters: tree,
        cursor: undefined,
      }),
    })
  )
  const stored = JSON.parse(
    window.localStorage.getItem(`${WORKSPACE_ID}:cases-filters:v1`) ?? "{}"
  )
  expect(stored.structuredFilters).toEqual(tree)
})
