import { act, renderHook, waitFor } from "@testing-library/react"
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
import { parseCaseReference } from "@/lib/cases/search-scope"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

jest.mock("@/client", () => ({
  casesSearchCases: jest.fn(),
  casesSearchCaseAggregates: jest.fn(),
}))

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
const OTHER_WORKSPACE_ID = "22222222-2222-4222-8222-222222222222"
const OLD_CASE: CaseReadMinimal = {
  id: "00000000-0000-4000-8000-000000000042",
  short_id: "CASE-0042",
  created_at: "2020-01-01T00:00:00Z",
  updated_at: "2020-01-01T00:00:00Z",
  summary: "Synthetic older sub-case",
  status: "closed",
  priority: "low",
  severity: "low",
  dropdown_values: [],
  parent: {
    id: "00000000-0000-4000-8000-000000000001",
    short_id: "CASE-0001",
    summary: "Synthetic parent case",
  },
  num_sub_cases: 0,
}

function setup(initialWorkspaceId = WORKSPACE_ID) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  let workspaceId = initialWorkspaceId
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
    changeWorkspace(nextWorkspaceId: string) {
      workspaceId = nextWorkspaceId
      hook.rerender()
    },
  }
}

function mockCasesResponse(response: CasesSearchCasesResponse) {
  jest
    .mocked(casesSearchCases)
    .mockImplementation(
      () => new CancelablePromise((resolve) => resolve(response))
    )
}

function mockAggregateResponse(response: CasesSearchCaseAggregatesResponse) {
  jest
    .mocked(casesSearchCaseAggregates)
    .mockImplementation(
      () => new CancelablePromise((resolve) => resolve(response))
    )
}

describe("complete case references", () => {
  it.each([" CASE-0042 ", "case-0042", "CASE-42", "CASE-12345678901"])(
    "recognizes %s as a short ID",
    (query) => {
      expect(parseCaseReference(query)).toEqual({
        type: "short_id",
        value: query.trim().toUpperCase(),
      })
    }
  )

  it("recognizes a canonical UUID", () => {
    expect(parseCaseReference(OLD_CASE.id)).toEqual({
      type: "uuid",
      value: OLD_CASE.id,
    })
  })

  it.each(["42", "CASE-", "CASE-0042 alert", "alert"])(
    "keeps ambiguous or partial query %s as text",
    (query) => expect(parseCaseReference(query)).toBeNull()
  )
})

describe("useCases search scope", () => {
  beforeEach(() => {
    window.localStorage.clear()
    jest.mocked(casesSearchCases).mockReset()
    mockCasesResponse({
      items: [],
      has_more: false,
      total_estimate: 0,
    })
    jest.mocked(casesSearchCaseAggregates).mockReset()
    mockAggregateResponse({
      total: 0,
      status_groups: {
        new: 0,
        in_progress: 0,
        on_hold: 0,
        resolved: 0,
        closed: 0,
        other: 0,
        unknown: 0,
      },
    })
  })

  it("keeps dates and explicit filters for text, while including sub-cases", async () => {
    const { result } = setup()
    act(() => {
      result.current.setStatusFilter(["new"])
      result.current.setPriorityFilter(["high"])
      result.current.setTagFilter(["synthetic-tag"])
      result.current.setSearchQuery("synthetic")
    })
    await waitFor(() =>
      expect(casesSearchCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          workspaceId: WORKSPACE_ID,
          searchTerm: "synthetic",
          startTime: expect.any(String),
          hierarchy: "all",
          status: ["new"],
          priority: ["high"],
          tags: ["synthetic-tag"],
        })
      )
    )
    expect(result.current.searchScope).toBe("view")
  })

  it("finds an older filtered-out sub-case by complete short ID", async () => {
    mockCasesResponse({
      items: [OLD_CASE],
      has_more: false,
      total_estimate: 1,
    })
    const { result } = setup()
    act(() => {
      result.current.setStatusFilter(["new"])
      result.current.setTagFilter(["synthetic-tag"])
      result.current.setUpdatedAfter({ type: "preset", value: "1d" })
      result.current.setSearchQuery("case-0042")
    })
    await waitFor(() => expect(result.current.searchScope).toBe("all"))
    await waitFor(() => expect(result.current.cases).toEqual([OLD_CASE]))
    expect(casesSearchCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        workspaceId: WORKSPACE_ID,
        shortId: "CASE-0042",
        searchTerm: undefined,
        hierarchy: "all",
      })
    )
    const request = jest.mocked(casesSearchCases).mock.calls.at(-1)?.[0]
    expect(request).not.toHaveProperty("startTime")
    expect(request).not.toHaveProperty("updatedAfter")
    expect(request).not.toHaveProperty("status")
    expect(request).not.toHaveProperty("tags")
    expect(result.current.searchScope).toBe("all")
    expect(result.current.totalFilteredCaseEstimate).toBe(1)
    expect(result.current.stageCounts?.closed).toBe(1)
    expect(result.current.stageCounts?.new).toBe(0)
    expect(result.current.filters.statusFilter).toEqual(["new"])
    expect(result.current.filters.createdAfter).toEqual({
      type: "preset",
      value: "1m",
    })
  })

  it("uses exact UUID equality rather than fuzzy text search or case viewing", async () => {
    const { result } = setup()
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    jest.mocked(casesSearchCaseAggregates).mockClear()
    mockCasesResponse({
      items: [OLD_CASE],
      has_more: false,
      total_estimate: 1,
    })
    act(() => result.current.setSearchQuery(OLD_CASE.id))
    await waitFor(() =>
      expect(result.current.totalFilteredCaseEstimate).toBe(1)
    )
    expect(casesSearchCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        workspaceId: WORKSPACE_ID,
        caseId: OLD_CASE.id,
        searchTerm: undefined,
        hierarchy: "all",
      })
    )
    expect(casesSearchCaseAggregates).not.toHaveBeenCalled()
  })

  it("allows exact-looking queries to explicitly search the current view", async () => {
    const { result } = setup()
    act(() => {
      result.current.setStatusFilter(["new"])
      result.current.setSearchQuery("CASE-0042")
      result.current.setSearchScope("view")
    })
    await waitFor(() =>
      expect(casesSearchCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          searchTerm: "CASE-0042",
          startTime: expect.any(String),
          status: ["new"],
        })
      )
    )
    expect(result.current.searchScope).toBe("view")
  })

  it("expands a text search to all cases and restores the view's filters", async () => {
    const { result } = setup()
    act(() => {
      result.current.setStatusFilter(["new"])
      result.current.setSearchQuery("synthetic")
      result.current.setSearchScope("all")
    })
    await waitFor(() =>
      expect(casesSearchCases).toHaveBeenLastCalledWith(
        expect.objectContaining({ searchTerm: "synthetic", hierarchy: "all" })
      )
    )
    expect(
      jest.mocked(casesSearchCases).mock.calls.at(-1)?.[0]
    ).not.toHaveProperty("startTime")
    act(() => result.current.setSearchScope("view"))
    await waitFor(() =>
      expect(casesSearchCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          searchTerm: "synthetic",
          status: ["new"],
          startTime: expect.any(String),
        })
      )
    )
  })

  it("returns no result for an ID in another workspace and isolates caches", async () => {
    jest.mocked(casesSearchCases).mockImplementation((request) => {
      const items = request.workspaceId === OTHER_WORKSPACE_ID ? [OLD_CASE] : []
      return new CancelablePromise((resolve) =>
        resolve({
          items,
          has_more: false,
          total_estimate: items.length,
        })
      )
    })
    const { result, changeWorkspace } = setup()
    act(() => result.current.setSearchQuery("CASE-0042"))
    await waitFor(() =>
      expect(result.current.totalFilteredCaseEstimate).toBe(0)
    )
    expect(result.current.cases).toEqual([])
    act(() => changeWorkspace(OTHER_WORKSPACE_ID))
    act(() => result.current.setSearchQuery("CASE-0042"))
    await waitFor(() => expect(result.current.cases).toEqual([OLD_CASE]))
    act(() => changeWorkspace(WORKSPACE_ID))
    expect(result.current.cases).toEqual([])
    await waitFor(() =>
      expect(result.current.totalFilteredCaseEstimate).toBe(0)
    )
  })

  it("returns to the default date and hierarchy scope after clearing lookup", async () => {
    const { result } = setup()
    act(() => result.current.setSearchQuery("CASE-0042"))
    await waitFor(() => expect(result.current.searchScope).toBe("all"))
    act(() => result.current.setSearchQuery(""))
    await waitFor(() =>
      expect(casesSearchCases).toHaveBeenCalledWith(
        expect.objectContaining({
          searchTerm: undefined,
          startTime: expect.any(String),
          hierarchy: "top_level",
        })
      )
    )
    expect(result.current.searchScope).toBe("view")
  })
})
