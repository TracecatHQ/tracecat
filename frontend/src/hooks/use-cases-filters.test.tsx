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

describe("useCases exact-first requests", () => {
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
  })

  it.each(["CASE-0042", OLD_CASE.id, "42", "CASE-"])(
    "delegates reference recognition for %s to the server with filters intact",
    async (query) => {
      const { result } = setup()
      act(() => {
        result.current.setStatusFilter(["new"])
        result.current.setTagFilter(["synthetic-tag"])
        result.current.setSearchQuery(query)
      })
      await waitFor(() =>
        expect(casesSearchCases).toHaveBeenLastCalledWith(
          expect.objectContaining({
            workspaceId: WORKSPACE_ID,
            searchTerm: query,
            promoteExactMatch: true,
            startTime: expect.any(String),
            status: ["new"],
            tags: ["synthetic-tag"],
          })
        )
      )
      const request = jest.mocked(casesSearchCases).mock.calls.at(-1)?.[0]
      expect(request).not.toHaveProperty("shortId")
      expect(request).not.toHaveProperty("caseId")
      expect(result.current.filters.createdAfter).toEqual({
        type: "preset",
        value: "1m",
      })
    }
  )

  it("can fetch a promoted case when ordinary enum filters match nothing", async () => {
    mockCasesResponse({
      items: [{ ...OLD_CASE, is_exact_match: true, is_outside_filters: true }],
      has_more: false,
    })
    const { result } = setup()
    act(() => {
      result.current.setStatusFilter([
        "new",
        "in_progress",
        "on_hold",
        "resolved",
        "closed",
        "unknown",
        "other",
      ])
      result.current.setStatusMode("exclude")
      result.current.setSearchQuery("CASE-0042")
    })
    await waitFor(() =>
      expect(casesSearchCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          searchTerm: "CASE-0042",
          promoteExactMatch: true,
          matchNone: true,
        })
      )
    )
    await waitFor(() => expect(result.current.cases).toHaveLength(1))
    expect(result.current.totalFilteredCaseEstimate).toBe(0)
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
    const promoted = {
      ...OLD_CASE,
      is_exact_match: true,
      is_outside_filters: true,
    }
    jest.mocked(casesSearchCases).mockImplementation((request) => {
      const items = request.searchTerm ? [promoted] : []
      return new CancelablePromise((resolve) =>
        resolve({ items, has_more: false, total_estimate: 0 })
      )
    })
    const { result } = setup()
    act(() => result.current.setSearchQuery("CASE-0042"))
    await waitFor(() => expect(result.current.cases).toEqual([promoted]))
    act(() => result.current.setSearchQuery(""))
    await waitFor(() => expect(result.current.cases).toEqual([]))
    expect(result.current.appliedSearchQuery).toBe("")
    expect(result.current.effectiveHierarchy).toBe("top_level")
    expect(result.current.filters.createdAfter).toEqual({
      type: "preset",
      value: "1m",
    })
  })
})
