/**
 * @jest-environment jsdom
 */

import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type { CasesListCaseRowsData, CaseTableRowRead } from "@/client"
import { casesListCaseRows } from "@/client"
import { useCaseRowsPagination } from "@/hooks/pagination/use-case-rows-pagination"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    casesListCaseRows: jest.fn(),
  }
})

const mockListCaseRows = casesListCaseRows as jest.MockedFunction<
  typeof casesListCaseRows
>

function makeLink(index: number): CaseTableRowRead {
  return {
    id: `link-${index}`,
    case_id: "case-1",
    table_id: "table-1",
    table_name: "Table 1",
    row_id: `row-${index}`,
    row_data: { value: index },
    is_row_available: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }
}

const FIRST_PAGE = {
  items: [makeLink(1)],
  next_cursor: "cursor-2",
  prev_cursor: null,
  has_more: true,
  has_previous: false,
  total_estimate: 3,
}

const SECOND_PAGE = {
  items: [makeLink(2)],
  next_cursor: null,
  prev_cursor: "cursor-1",
  has_more: false,
  has_previous: true,
  total_estimate: 3,
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

function lastCallParams(): CasesListCaseRowsData {
  const call = mockListCaseRows.mock.calls.at(-1)
  if (!call) throw new Error("casesListCaseRows was not called")
  return call[0]
}

describe("useCaseRowsPagination", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockListCaseRows.mockImplementation(
      ({ cursor }) =>
        Promise.resolve(
          cursor === "cursor-2" ? SECOND_PAGE : FIRST_PAGE
        ) as unknown as ReturnType<typeof casesListCaseRows>
    )
  })

  it("requests the first page for the case and table", async () => {
    const { result } = renderHook(
      () =>
        useCaseRowsPagination({
          caseId: "case-1",
          tableId: "table-1",
          workspaceId: "ws-1",
        }),
      { wrapper: createWrapper() }
    )

    await waitFor(() => {
      expect(result.current.data).toHaveLength(1)
    })

    expect(mockListCaseRows).toHaveBeenCalledTimes(1)
    expect(mockListCaseRows).toHaveBeenCalledWith(
      expect.objectContaining({
        caseId: "case-1",
        tableId: "table-1",
        workspaceId: "ws-1",
        limit: 20,
        cursor: null,
      })
    )
  })

  it("walks forward with next_cursor and back to the first page", async () => {
    const { result } = renderHook(
      () =>
        useCaseRowsPagination({
          caseId: "case-1",
          tableId: "table-1",
          workspaceId: "ws-1",
        }),
      { wrapper: createWrapper() }
    )

    await waitFor(() => {
      expect(result.current.hasNextPage).toBe(true)
    })

    act(() => {
      result.current.goToNextPage()
    })

    await waitFor(() => {
      expect(result.current.data[0]?.row_id).toBe("row-2")
    })
    expect(lastCallParams().cursor).toBe("cursor-2")
    expect(result.current.currentPage).toBe(1)
    expect(result.current.hasPreviousPage).toBe(true)

    act(() => {
      result.current.goToPreviousPage()
    })

    await waitFor(() => {
      expect(result.current.data[0]?.row_id).toBe("row-1")
    })
    expect(result.current.currentPage).toBe(0)
    expect(result.current.hasPreviousPage).toBe(false)
  })

  it("mirrors has_more and total_estimate from the response", async () => {
    const { result } = renderHook(
      () =>
        useCaseRowsPagination({
          caseId: "case-1",
          tableId: "table-1",
          workspaceId: "ws-1",
        }),
      { wrapper: createWrapper() }
    )

    await waitFor(() => {
      expect(result.current.totalEstimate).toBe(3)
    })
    expect(result.current.hasNextPage).toBe(true)

    act(() => {
      result.current.goToNextPage()
    })

    await waitFor(() => {
      expect(result.current.data[0]?.row_id).toBe("row-2")
    })
    expect(result.current.hasNextPage).toBe(false)
    expect(result.current.totalEstimate).toBe(3)
  })

  it("resets to the first page when the limit changes", async () => {
    const { result, rerender } = renderHook(
      ({ limit }: { limit: number }) =>
        useCaseRowsPagination({
          caseId: "case-1",
          tableId: "table-1",
          workspaceId: "ws-1",
          limit,
        }),
      { wrapper: createWrapper(), initialProps: { limit: 20 } }
    )

    await waitFor(() => {
      expect(result.current.hasNextPage).toBe(true)
    })
    act(() => {
      result.current.goToNextPage()
    })
    await waitFor(() => {
      expect(result.current.currentPage).toBe(1)
    })

    rerender({ limit: 50 })

    await waitFor(() => {
      expect(lastCallParams()).toEqual(
        expect.objectContaining({ limit: 50, cursor: null })
      )
    })
    expect(result.current.currentPage).toBe(0)
  })

  it("fires nothing while disabled", async () => {
    const { result } = renderHook(
      () =>
        useCaseRowsPagination({
          caseId: "case-1",
          tableId: "table-1",
          workspaceId: "ws-1",
          enabled: false,
        }),
      { wrapper: createWrapper() }
    )

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mockListCaseRows).not.toHaveBeenCalled()
    expect(result.current.data).toEqual([])
  })
})
