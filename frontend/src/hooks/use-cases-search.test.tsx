import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  type CaseReadMinimal,
  type CasesSearchCaseAggregatesResponse,
  type CasesSearchCasesResponse,
  casesSearchCaseAggregates,
  casesSearchCases,
} from "@/client"
import { CancelablePromise } from "@/client/core/CancelablePromise"
import { useCases } from "@/hooks/use-cases"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

jest.mock("@/client", () => ({
  casesSearchCases: jest.fn(),
  casesSearchCaseAggregates: jest.fn(),
}))

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
const EMPTY_ROWS: CasesSearchCasesResponse = { items: [], has_more: false }
const EMPTY_COUNTS: CasesSearchCaseAggregatesResponse = {
  total: 0,
  status_groups: { new: 0, in_progress: 0, on_hold: 0, resolved: 0, closed: 0 },
}

function resolved<T>(value: T) {
  return new CancelablePromise<T>((resolve) => resolve(value))
}

function deferred<T>() {
  let resolveRequest: (value: T) => void = () => undefined
  let rejectRequest: (reason: unknown) => void = () => undefined
  const request = new CancelablePromise<T>((resolve, reject) => {
    resolveRequest = resolve
    rejectRequest = reject
  })
  return {
    request,
    resolve: resolveRequest,
    reject: rejectRequest,
    cancel: jest.spyOn(request, "cancel"),
  }
}

async function advance(ms = 1) {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(ms)
  })
}

function setup(
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
) {
  let workspaceId = WORKSPACE_ID
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <WorkspaceIdProvider workspaceId={workspaceId}>
          {children}
        </WorkspaceIdProvider>
      </QueryClientProvider>
    )
  }
  const hook = renderHook(() => useCases({ autoRefresh: false }), {
    wrapper: Wrapper,
  })
  return {
    ...hook,
    queryClient,
    changeWorkspace(nextWorkspaceId: string) {
      workspaceId = nextWorkspaceId
      hook.rerender()
    },
  }
}

describe("case search applied identity and cancellation", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    window.localStorage.clear()
    jest.mocked(casesSearchCases).mockReset()
    jest.mocked(casesSearchCases).mockImplementation(() => resolved(EMPTY_ROWS))
    jest.mocked(casesSearchCaseAggregates).mockReset()
    jest
      .mocked(casesSearchCaseAggregates)
      .mockImplementation(() => resolved(EMPTY_COUNTS))
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it("updates input synchronously and applies one row/count search after 300 ms idle", async () => {
    const { result, queryClient } = setup()
    await advance()
    jest.mocked(casesSearchCases).mockClear()
    jest.mocked(casesSearchCaseAggregates).mockClear()
    act(() => result.current.setSearchQuery("s"))
    await advance(100)
    act(() => result.current.setSearchQuery("sy"))
    await advance(100)
    act(() => result.current.setSearchQuery("synthetic"))
    expect(result.current.filters.searchQuery).toBe("synthetic")
    expect(result.current.appliedSearchQuery).toBe("")
    expect(result.current.isSearchPending).toBe(true)
    await advance(299)
    expect(casesSearchCases).not.toHaveBeenCalled()
    expect(casesSearchCaseAggregates).not.toHaveBeenCalled()
    await advance(1)
    expect(casesSearchCases).toHaveBeenCalledTimes(1)
    expect(casesSearchCaseAggregates).toHaveBeenCalledTimes(1)
    const rowRequest = jest.mocked(casesSearchCases).mock.calls[0][0]
    const countRequest = jest.mocked(casesSearchCaseAggregates).mock.calls[0][0]
    expect(rowRequest).toMatchObject(countRequest)
    expect(rowRequest.searchTerm).toBe("synthetic")
    expect(result.current.appliedSearchQuery).toBe("synthetic")
    expect(result.current.isSearchPending).toBe(false)
    const keys = queryClient
      .getQueryCache()
      .getAll()
      .map((query) => query.queryKey)
    const rowsKey = keys.find(
      (key) =>
        key[1] === WORKSPACE_ID &&
        JSON.parse(String(key[2])).filtersKey.includes("synthetic")
    )
    const countsKey = keys.find(
      (key) => key[1] === "aggregate" && String(key[3]).includes("synthetic")
    )
    expect(JSON.parse(String(rowsKey?.[2])).filtersKey).toBe(countsKey?.[3])
  })

  it("keeps exact promotion on the applied term during typing", async () => {
    const { result } = setup()
    await advance()
    act(() => result.current.setSearchQuery("synthetic"))
    await advance(300)
    act(() => result.current.setSearchQuery("CASE-0042"))
    expect(result.current.appliedSearchQuery).toBe("synthetic")
    await advance(299)
    expect(jest.mocked(casesSearchCases).mock.calls.at(-1)?.[0]).toMatchObject({
      searchTerm: "synthetic",
      startTime: expect.any(String),
    })
    await advance(1)
    expect(jest.mocked(casesSearchCases).mock.calls.at(-1)?.[0]).toMatchObject({
      searchTerm: "CASE-0042",
      promoteExactMatch: true,
      startTime: expect.any(String),
      hierarchy: "all",
    })
    act(() => result.current.setSearchQuery("synthetic again"))
    expect(result.current.appliedSearchQuery).toBe("CASE-0042")
    await advance(300)
    expect(jest.mocked(casesSearchCases).mock.calls.at(-1)?.[0]).toMatchObject({
      searchTerm: "synthetic again",
      startTime: expect.any(String),
    })
  })

  it("preserves loaded pages and requests when an empty search is cleared again", async () => {
    jest.mocked(casesSearchCases).mockImplementation((request) =>
      resolved<CasesSearchCasesResponse>({
        items: [
          {
            ...CASE,
            id: request.cursor
              ? "00000000-0000-4000-8000-000000000043"
              : CASE.id,
          },
        ],
        has_more: !request.cursor,
        next_cursor: request.cursor ? null : "synthetic-cursor",
      })
    )
    const { result } = setup()
    await advance()
    act(() => result.current.fetchNextPage())
    await advance()
    expect(result.current.cases).toHaveLength(2)
    const rowRequests = jest.mocked(casesSearchCases).mock.calls.length
    const countRequests = jest.mocked(casesSearchCaseAggregates).mock.calls
      .length
    act(() => result.current.setSearchQuery(""))
    await advance(300)
    expect(result.current.cases).toHaveLength(2)
    expect(casesSearchCases).toHaveBeenCalledTimes(rowRequests)
    expect(casesSearchCaseAggregates).toHaveBeenCalledTimes(countRequests)
  })

  it("starts a newly applied search at page one after remounting with a live cache", async () => {
    jest.mocked(casesSearchCases).mockImplementation((request) =>
      resolved<CasesSearchCasesResponse>({
        items: [
          {
            ...CASE,
            id: request.cursor
              ? "00000000-0000-4000-8000-000000000043"
              : CASE.id,
          },
        ],
        has_more: !request.cursor,
        next_cursor: request.cursor ? null : "synthetic-cursor",
      })
    )
    const first = setup()
    await advance()
    act(() =>
      first.result.current.setCreatedAfter({ type: "preset", value: null })
    )
    await advance()
    act(() => first.result.current.setSearchQuery("synthetic alpha"))
    await advance(300)
    await advance()
    act(() => first.result.current.fetchNextPage())
    await advance()
    expect(first.result.current.cases).toHaveLength(2)
    act(() => first.result.current.setSearchQuery("synthetic bravo"))
    await advance(300)
    first.unmount()

    const second = setup(first.queryClient)
    await advance()
    expect(second.result.current.appliedSearchQuery).toBe("synthetic bravo")
    act(() => second.result.current.setSearchQuery("synthetic alpha"))
    await advance(300)
    await advance()
    expect(second.result.current.cases).toHaveLength(1)
    expect(casesSearchCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        searchTerm: "synthetic alpha",
        cursor: undefined,
      })
    )
  })

  it("clears immediately, cancels pending application, and starts at page one", async () => {
    jest.mocked(casesSearchCases).mockImplementation((request) =>
      resolved<CasesSearchCasesResponse>({
        items: [
          {
            ...CASE,
            id: request.cursor
              ? "00000000-0000-4000-8000-000000000043"
              : CASE.id,
          },
        ],
        has_more: !request.cursor,
        next_cursor: request.cursor ? null : "synthetic-cursor",
      })
    )
    const { result } = setup()
    await advance()
    act(() => result.current.setCreatedAfter({ type: "preset", value: null }))
    await advance()
    act(() => result.current.fetchNextPage())
    await advance()
    expect(result.current.cases).toHaveLength(2)
    act(() => result.current.setSearchQuery("synthetic"))
    await advance(300)
    await advance()
    act(() => result.current.fetchNextPage())
    await advance()
    expect(result.current.cases).toHaveLength(2)
    act(() => result.current.setSearchQuery("synthetic pending"))
    await advance(150)
    jest.mocked(casesSearchCases).mockClear()
    act(() => result.current.setSearchQuery(""))
    expect(result.current.appliedSearchQuery).toBe("")
    expect(result.current.isSearchPending).toBe(false)
    await advance()
    expect(result.current.cases).toHaveLength(1)
    expect(casesSearchCases).toHaveBeenCalledTimes(1)
    expect(casesSearchCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        searchTerm: undefined,
        cursor: undefined,
      })
    )
    await advance(500)
    expect(casesSearchCases).toHaveBeenCalledTimes(1)
  })

  it("aborts superseded rows and counts and ignores their late outcomes", async () => {
    const oldRows = deferred<CasesSearchCasesResponse>()
    const oldCounts = deferred<CasesSearchCaseAggregatesResponse>()
    const newerRows = deferred<CasesSearchCasesResponse>()
    const newerCounts = deferred<CasesSearchCaseAggregatesResponse>()
    jest.mocked(casesSearchCases).mockImplementation((request) => {
      if (request.searchTerm === "old synthetic") return oldRows.request
      if (request.searchTerm === "new synthetic") return newerRows.request
      return resolved(EMPTY_ROWS)
    })
    jest.mocked(casesSearchCaseAggregates).mockImplementation((request) => {
      if (request.searchTerm === "old synthetic") return oldCounts.request
      if (request.searchTerm === "new synthetic") return newerCounts.request
      return resolved(EMPTY_COUNTS)
    })
    const { result } = setup()
    await advance()
    act(() => result.current.setSearchQuery("old synthetic"))
    await advance(300)
    expect(result.current.isLoading).toBe(true)
    act(() => result.current.setSearchQuery("new synthetic"))
    await advance(299)
    expect(oldRows.cancel).not.toHaveBeenCalled()
    await advance(1)
    expect(oldRows.cancel).toHaveBeenCalledTimes(1)
    expect(oldCounts.cancel).toHaveBeenCalledTimes(1)
    act(() => {
      newerRows.resolve({ items: [CASE], has_more: false })
      newerCounts.resolve({
        ...EMPTY_COUNTS,
        total: 1,
        status_groups: { new: 1 },
      })
    })
    await advance()
    expect(result.current.cases).toEqual([CASE])
    expect(result.current.totalFilteredCaseEstimate).toBe(1)
    expect(result.current.isLoading).toBe(false)
    act(() => {
      oldRows.reject(new Error("Synthetic obsolete error"))
      oldCounts.resolve({ ...EMPTY_COUNTS, total: 99 })
    })
    await advance()
    expect(result.current.cases).toEqual([CASE])
    expect(result.current.totalFilteredCaseEstimate).toBe(1)
    expect(result.current.error).toBeNull()
  })

  it("applies a new workspace's saved search immediately and discards the old pending text", async () => {
    const { result, changeWorkspace } = setup()
    await advance()
    act(() => result.current.setSearchQuery("CASE-0042"))
    await advance(300)
    act(() => result.current.setSearchQuery("old workspace pending"))
    window.localStorage.setItem(
      `${OTHER_WORKSPACE_ID}:cases-filters:v1`,
      JSON.stringify({ searchQuery: "new workspace synthetic" })
    )
    act(() => changeWorkspace(OTHER_WORKSPACE_ID))
    expect(result.current.appliedSearchQuery).toBe("new workspace synthetic")
    await advance()
    expect(casesSearchCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        workspaceId: OTHER_WORKSPACE_ID,
        searchTerm: "new workspace synthetic",
      })
    )
    await advance(500)
    expect(result.current.appliedSearchQuery).toBe("new workspace synthetic")
  })

  it("shows loading and empty state for the applied query rather than completed old rows", async () => {
    const rows = deferred<CasesSearchCasesResponse>()
    const counts = deferred<CasesSearchCaseAggregatesResponse>()
    jest.mocked(casesSearchCases).mockImplementation((request) => {
      if (request.searchTerm === "new synthetic") return rows.request
      return resolved<CasesSearchCasesResponse>({
        items: [CASE],
        has_more: false,
      })
    })
    jest.mocked(casesSearchCaseAggregates).mockImplementation((request) => {
      if (request.searchTerm === "new synthetic") return counts.request
      return resolved<CasesSearchCaseAggregatesResponse>({
        ...EMPTY_COUNTS,
        total: 1,
      })
    })
    const { result } = setup()
    await advance()
    expect(result.current.cases).toEqual([CASE])
    act(() => result.current.setSearchQuery("new synthetic"))
    expect(result.current.cases).toEqual([CASE])
    expect(result.current.isSearchPending).toBe(true)
    await advance(300)
    expect(result.current.cases).toEqual([])
    expect(result.current.totalFilteredCaseEstimate).toBeNull()
    expect(result.current.isLoading).toBe(true)
    act(() => {
      rows.resolve(EMPTY_ROWS)
      counts.resolve(EMPTY_COUNTS)
    })
    await advance()
    expect(result.current.cases).toEqual([])
    expect(result.current.totalFilteredCaseEstimate).toBe(0)
    expect(result.current.isLoading).toBe(false)
  })

  it("aborts in-flight rows and counts immediately when cleared", async () => {
    const rows = deferred<CasesSearchCasesResponse>()
    const counts = deferred<CasesSearchCaseAggregatesResponse>()
    jest
      .mocked(casesSearchCases)
      .mockImplementation((request) =>
        request.searchTerm ? rows.request : resolved(EMPTY_ROWS)
      )
    jest
      .mocked(casesSearchCaseAggregates)
      .mockImplementation((request) =>
        request.searchTerm ? counts.request : resolved(EMPTY_COUNTS)
      )
    const { result } = setup()
    await advance()
    act(() => result.current.setSearchQuery("synthetic"))
    await advance(300)
    act(() => result.current.setSearchQuery(""))
    expect(rows.cancel).toHaveBeenCalledTimes(1)
    expect(counts.cancel).toHaveBeenCalledTimes(1)
    expect(result.current.appliedSearchQuery).toBe("")
    await advance()
    expect(result.current.isLoading).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it("cancels both requests and pending application when unmounted", async () => {
    const rows = deferred<CasesSearchCasesResponse>()
    const counts = deferred<CasesSearchCaseAggregatesResponse>()
    jest.mocked(casesSearchCases).mockImplementation(() => rows.request)
    jest
      .mocked(casesSearchCaseAggregates)
      .mockImplementation(() => counts.request)
    const { result, unmount } = setup()
    act(() => result.current.setSearchQuery("synthetic pending"))
    unmount()
    expect(rows.cancel).toHaveBeenCalledTimes(1)
    expect(counts.cancel).toHaveBeenCalledTimes(1)
    await advance(500)
    expect(casesSearchCases).toHaveBeenCalledTimes(1)
    expect(casesSearchCaseAggregates).toHaveBeenCalledTimes(1)
  })
})
