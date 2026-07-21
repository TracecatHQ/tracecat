/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type { CaseTableRowRead } from "@/client"
import { casesListCaseRows } from "@/client"
import { useListCaseRows } from "@/lib/hooks"

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

function createRow(index: number): CaseTableRowRead {
  return {
    id: `link-${index}`,
    case_id: "case-1",
    table_id: "table-1",
    table_name: "Table 1",
    row_id: `row-${index}`,
    row_data: { value: index },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }
}

describe("useListCaseRows", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("concatenates every cursor page in order", async () => {
    mockListCaseRows
      .mockResolvedValueOnce({
        items: [createRow(1), createRow(2)],
        next_cursor: "cursor-2",
      })
      .mockResolvedValueOnce({
        items: [createRow(3)],
        next_cursor: "cursor-3",
      })
      .mockResolvedValueOnce({
        items: [createRow(4), createRow(5)],
        next_cursor: null,
      })

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    function wrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      )
    }

    const { result } = renderHook(
      () => useListCaseRows("case-1", "workspace-1"),
      { wrapper }
    )

    await waitFor(() => {
      expect(result.current.caseRows).toHaveLength(5)
    })

    expect(result.current.caseRows.map((row) => row.row_id)).toEqual([
      "row-1",
      "row-2",
      "row-3",
      "row-4",
      "row-5",
    ])
    expect(mockListCaseRows).toHaveBeenNthCalledWith(1, {
      caseId: "case-1",
      workspaceId: "workspace-1",
      limit: 200,
      cursor: undefined,
    })
    expect(mockListCaseRows).toHaveBeenNthCalledWith(2, {
      caseId: "case-1",
      workspaceId: "workspace-1",
      limit: 200,
      cursor: "cursor-2",
    })
    expect(mockListCaseRows).toHaveBeenNthCalledWith(3, {
      caseId: "case-1",
      workspaceId: "workspace-1",
      limit: 200,
      cursor: "cursor-3",
    })
  })
})
