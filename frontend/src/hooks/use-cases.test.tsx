import { act, renderHook } from "@testing-library/react"
import axios from "axios"
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

jest.mock("@/client", () => ({
  casesSearchCases: jest.fn(),
  casesSearchCaseAggregates: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-test",
}))

const searchCases = jest.mocked(casesSearchCases)
const searchAggregates = jest.mocked(casesSearchCaseAggregates)

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
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return {
    ...renderHook(() => useCases({ autoRefresh: false }), { wrapper }),
    client,
  }
}

async function advance(ms = 1) {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(ms)
  })
}

function searchTerms(mock: jest.Mock) {
  return mock.mock.calls.map(([params]) => params.searchTerm)
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
  searchCases.mockImplementation(sdk.casesSearchCases)
  searchAggregates.mockImplementation(sdk.casesSearchCaseAggregates)
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
      searchTerm: CASE.short_id,
      promoteExactMatch: true,
    })
  )
  expect(searchAggregates).toHaveBeenLastCalledWith(
    expect.objectContaining({ searchTerm: CASE.short_id })
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
          id: request.cursor ? "00000000-0000-4000-8000-000000000043" : CASE.id,
        },
      ],
      has_more: !request.cursor,
      next_cursor: request.cursor ? null : "synthetic-cursor",
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
    expect.objectContaining({ searchTerm: undefined, cursor: undefined })
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
    expect.objectContaining({ searchTerm: "phishing", cursor: undefined })
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
    jest.mocked(casesSearchCaseAggregates).mockImplementation(() => counts)
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
        next_cursor: request.cursor ? null : "synthetic-continuation",
        // A cached legacy page total must not become another count source.
        total_estimate: request.cursor ? null : 999,
      })
    )
    const { result } = setup()
    await advance()
    expect(casesSearchCases).toHaveBeenCalledTimes(1)
    expect(casesSearchCaseAggregates).toHaveBeenCalledTimes(1)
    expect(casesSearchCases).toHaveBeenLastCalledWith(
      expect.objectContaining({ includeCount: false, cursor: undefined })
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
    expect(casesSearchCases).toHaveBeenCalledTimes(2)
    expect(casesSearchCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        includeCount: false,
        cursor: "synthetic-continuation",
      })
    )
    expect(casesSearchCaseAggregates).toHaveBeenCalledTimes(1)
    expect(result.current.totalFilteredCaseEstimate).toBe(12)
    expect(result.current.stageCounts?.new).toBe(12)
  })

  it("keeps successful rows when population counts exhaust retries", async () => {
    jest.mocked(casesSearchCases).mockImplementation(() =>
      resolved<CasesSearchCasesResponse>({
        items: [CASE],
        has_more: true,
        next_cursor: "synthetic-next-page",
        total_estimate: null,
      })
    )
    jest.mocked(casesSearchCaseAggregates).mockImplementation(
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
    expect(casesSearchCaseAggregates).toHaveBeenCalledTimes(4)
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
    jest.mocked(casesSearchCaseAggregates).mockImplementation(() =>
      resolved<CasesSearchCaseAggregatesResponse>({
        total: 12,
        status_groups: { new: 12 },
      })
    )
    const { result } = setup()
    await advance()
    expect(result.current.totalFilteredCaseEstimate).toBe(12)
    jest.mocked(casesSearchCaseAggregates).mockImplementation(
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
