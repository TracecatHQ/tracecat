import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react"
import type { ReactNode } from "react"
import { type TableColumnRead, tablesListRows } from "@/client"
import { isSearchableColumn } from "@/components/tables/ag-grid-column-defs"
import {
  ColumnSortButton,
  nextSortDirection,
} from "@/components/tables/column-sort-button"
import {
  type UseTablesPaginationParams,
  useTablesPagination,
} from "@/hooks/pagination/use-tables-pagination"
import { isStaleColumnError, toRowSearchParams } from "@/hooks/use-row-search"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({ tablesListRows: jest.fn() }))

test("only a 400 naming a bad column counts as a stale column", () => {
  const apiError = (status: number, detail: string) =>
    ({ status, body: { detail } }) as Parameters<typeof isStaleColumnError>[0]

  expect(isStaleColumnError(apiError(400, "Invalid search_column: host"))).toBe(
    true
  )
  expect(
    isStaleColumnError(apiError(400, "Invalid order_by column: host"))
  ).toBe(true)
  expect(
    isStaleColumnError(
      apiError(400, "Column score does not support text search")
    )
  ).toBe(true)
  expect(
    isStaleColumnError(
      apiError(400, "Search term cannot exceed 1000 characters")
    )
  ).toBe(false)
  expect(isStaleColumnError(apiError(500, "Invalid search_column: host"))).toBe(
    false
  )
  expect(isStaleColumnError(null)).toBe(false)
})

function column(type: TableColumnRead["type"]): TableColumnRead {
  return { id: `${type}-id`, name: type.toLowerCase(), type, is_index: false }
}

test("only text, select, multi-select and JSON columns are searchable", () => {
  const searchable = (
    [
      "TEXT",
      "INTEGER",
      "NUMERIC",
      "DATE",
      "BOOLEAN",
      "TIMESTAMPTZ",
      "JSONB",
      "SELECT",
      "MULTI_SELECT",
    ] as const
  ).filter((type) => isSearchableColumn(column(type)))
  expect(searchable).toEqual(["TEXT", "JSONB", "SELECT", "MULTI_SELECT"])
})

test("a search is sent trimmed, and only with both a term and a column", () => {
  expect(toRowSearchParams({ column: "title", term: "  abc " })).toEqual({
    searchTerm: "abc",
    searchColumn: "title",
  })
  const none = { searchTerm: null, searchColumn: null }
  expect(toRowSearchParams({ column: "title", term: "   " })).toEqual(none)
  expect(toRowSearchParams({ column: null, term: "abc" })).toEqual(none)
})

test("the sort cycle runs ascending, descending, then none", () => {
  expect(nextSortDirection(null)).toBe("asc")
  expect(nextSortDirection("asc")).toBe("desc")
  expect(nextSortDirection("desc")).toBe(false)
})

test("the sort button requests the next step for its own column", () => {
  const onSortChange = jest.fn()
  const { rerender } = render(
    <ColumnSortButton
      columnName="title"
      sort={{ orderBy: null, sort: null }}
      onSortChange={onSortChange}
    />
  )
  const button = screen.getByRole("button")
  expect(button).toHaveAttribute("data-sort", "none")
  fireEvent.click(button)
  expect(onSortChange).toHaveBeenLastCalledWith("title", "asc")

  rerender(
    <ColumnSortButton
      columnName="title"
      sort={{ orderBy: "title", sort: "desc" }}
      onSortChange={onSortChange}
    />
  )
  expect(button).toHaveAttribute("data-sort", "desc")
  fireEvent.click(button)
  expect(onSortChange).toHaveBeenLastCalledWith("title", false)

  // Another column holding the sort leaves this one unsorted.
  rerender(
    <ColumnSortButton
      columnName="title"
      sort={{ orderBy: "severity", sort: "asc" }}
      onSortChange={onSortChange}
    />
  )
  expect(button).toHaveAttribute("data-sort", "none")
  fireEvent.click(button)
  expect(onSortChange).toHaveBeenLastCalledWith("title", "asc")
})

test("rows pagination sends sort and search, and each resets the cursor", async () => {
  const listRows = jest.mocked(tablesListRows)
  listRows.mockResolvedValue({
    items: [{ id: "row-1", created_at: "", updated_at: "" }],
    next_cursor: "cursor-2",
    has_more: true,
  } as Awaited<ReturnType<typeof tablesListRows>>)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  const base: UseTablesPaginationParams = {
    tableId: "table-synthetic",
    workspaceId: "workspace-synthetic",
    limit: 1,
  }
  const { result, rerender } = renderHook(
    (props: UseTablesPaginationParams) => useTablesPagination(props),
    { wrapper, initialProps: base }
  )

  async function expectLastRequest(expected: Record<string, unknown>) {
    await waitFor(() =>
      expect(listRows).toHaveBeenLastCalledWith(
        expect.objectContaining(expected)
      )
    )
  }
  async function goToSecondPage() {
    await waitFor(() => expect(result.current.hasNextPage).toBe(true))
    act(() => result.current.goToNextPage())
    await expectLastRequest({ cursor: "cursor-2" })
    expect(result.current.currentPage).toBe(1)
  }

  await expectLastRequest({
    cursor: null,
    orderBy: null,
    sort: null,
    searchTerm: null,
    searchColumn: null,
  })

  await goToSecondPage()
  act(() => result.current.setSorting("title", "desc"))
  await expectLastRequest({ cursor: null, orderBy: "title", sort: "desc" })
  expect(result.current.currentPage).toBe(0)

  await goToSecondPage()
  rerender({ ...base, searchTerm: "abc", searchColumn: "title" })
  await expectLastRequest({
    cursor: null,
    orderBy: "title",
    sort: "desc",
    searchTerm: "abc",
    searchColumn: "title",
  })
  expect(result.current.currentPage).toBe(0)
})
