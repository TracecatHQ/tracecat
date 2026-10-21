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
import { FilteredRowsEmptyOverlay } from "@/components/tables/column-header-filter"
import {
  ColumnSortButton,
  nextSortDirection,
} from "@/components/tables/column-sort-button"
import {
  type TableRowQuery,
  TableRowQueryContext,
} from "@/components/tables/table-row-query-context"
import {
  type UseTablesPaginationParams,
  useTablesPagination,
} from "@/hooks/pagination/use-tables-pagination"
import {
  isStaleColumnError,
  type RowSearch,
  toRowSearchParams,
  useShownRowSearch,
} from "@/hooks/use-row-search"
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

type RowsPage = Awaited<ReturnType<typeof tablesListRows>>

function rowsPage(ids: string[], nextCursor: string | null = null): RowsPage {
  return {
    items: ids.map((id) => ({ id, created_at: "", updated_at: "" })),
    next_cursor: nextCursor,
    has_more: nextCursor !== null,
  } as RowsPage
}

/** A rows request the test settles by hand. */
function pendingRowsPage() {
  let resolve!: (page: RowsPage) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<RowsPage>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function renderTablesPagination(initialProps: UseTablesPaginationParams) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return renderHook(
    (props: UseTablesPaginationParams) => useTablesPagination(props),
    { wrapper, initialProps }
  )
}

const TABLE_A: UseTablesPaginationParams = {
  tableId: "table-a",
  workspaceId: "workspace-synthetic",
  limit: 2,
}

test("the previous rows stay up while a search or sort loads, and paging waits", async () => {
  const listRows = jest.mocked(tablesListRows)
  listRows.mockReset()
  listRows.mockResolvedValueOnce(rowsPage(["row-1", "row-2"], "cursor-2"))
  const { result, rerender } = renderTablesPagination(TABLE_A)
  await waitFor(() => expect(result.current.data).toHaveLength(2))
  expect(result.current.isPlaceholderData).toBe(false)

  // A search: the old rows stand in until the new ones land.
  const search = pendingRowsPage()
  listRows.mockReturnValueOnce(
    search.promise as ReturnType<typeof tablesListRows>
  )
  rerender({ ...TABLE_A, searchTerm: "abc", searchColumn: "title" })
  expect(result.current.data.map((row) => row.id)).toEqual(["row-1", "row-2"])
  expect(result.current.isPlaceholderData).toBe(true)
  expect(result.current.isLoading).toBe(false)
  await waitFor(() => expect(listRows).toHaveBeenCalledTimes(2))

  // The cursor on screen belongs to the unsearched rows: next must not use it.
  expect(result.current.hasNextPage).toBe(true)
  act(() => result.current.goToNextPage())
  expect(result.current.currentPage).toBe(0)
  expect(listRows).toHaveBeenCalledTimes(2)
  expect(listRows).toHaveBeenLastCalledWith(
    expect.objectContaining({ cursor: null, searchTerm: "abc" })
  )

  await act(async () => search.resolve(rowsPage(["row-9"])))
  await waitFor(() => expect(result.current.isPlaceholderData).toBe(false))
  expect(result.current.data.map((row) => row.id)).toEqual(["row-9"])
  expect(result.current.hasNextPage).toBe(false)

  // A sort: same again, and an empty answer empties the rows once it lands.
  const sort = pendingRowsPage()
  listRows.mockReturnValueOnce(
    sort.promise as ReturnType<typeof tablesListRows>
  )
  act(() => result.current.setSorting("title", "asc"))
  expect(result.current.data.map((row) => row.id)).toEqual(["row-9"])
  expect(result.current.isPlaceholderData).toBe(true)
  await act(async () => sort.resolve(rowsPage([])))
  await waitFor(() => expect(result.current.isPlaceholderData).toBe(false))
  expect(result.current.data).toEqual([])
  expect(result.current.error).toBeNull()
})

test("a failed search drops the previous rows and surfaces its error", async () => {
  const listRows = jest.mocked(tablesListRows)
  listRows.mockReset()
  listRows.mockResolvedValueOnce(rowsPage(["row-1"]))
  const { result, rerender } = renderTablesPagination(TABLE_A)
  await waitFor(() => expect(result.current.data).toHaveLength(1))

  const failure = {
    status: 400,
    body: { detail: "Invalid search_column: gone" },
  }
  listRows.mockRejectedValue(failure)
  // The cursor hook's queries retry with a backoff before they fail.
  jest.useFakeTimers()
  try {
    rerender({ ...TABLE_A, searchTerm: "abc", searchColumn: "gone" })
    expect(result.current.isPlaceholderData).toBe(true)
    expect(result.current.error).toBeNull()

    await act(async () => {
      await jest.advanceTimersByTimeAsync(30_000)
    })
  } finally {
    jest.useRealTimers()
  }
  expect(result.current.error).toBe(failure)
  expect(result.current.isPlaceholderData).toBe(false)
  expect(result.current.data).toEqual([])
})

test("another table's rows are never kept as a placeholder", async () => {
  const listRows = jest.mocked(tablesListRows)
  listRows.mockReset()
  listRows.mockResolvedValueOnce(rowsPage(["a-1", "a-2"]))
  const { result, rerender } = renderTablesPagination(TABLE_A)
  await waitFor(() => expect(result.current.data).toHaveLength(2))

  const otherTable = pendingRowsPage()
  listRows.mockReturnValueOnce(
    otherTable.promise as ReturnType<typeof tablesListRows>
  )
  rerender({ ...TABLE_A, tableId: "table-b" })
  expect(result.current.data).toEqual([])
  expect(result.current.isPlaceholderData).toBe(false)
  expect(result.current.isLoading).toBe(true)

  await act(async () => otherTable.resolve(rowsPage(["b-1"])))
  await waitFor(() =>
    expect(result.current.data.map((row) => row.id)).toEqual(["b-1"])
  )

  // Nor when the switch happens while placeholder rows are already up.
  listRows.mockReturnValue(
    pendingRowsPage().promise as ReturnType<typeof tablesListRows>
  )
  rerender({
    ...TABLE_A,
    tableId: "table-b",
    searchTerm: "abc",
    searchColumn: "title",
  })
  expect(result.current.isPlaceholderData).toBe(true)
  expect(result.current.data.map((row) => row.id)).toEqual(["b-1"])
  rerender({ ...TABLE_A, tableId: "table-c" })
  expect(result.current.data).toEqual([])
  expect(result.current.isPlaceholderData).toBe(false)
})

test("the shown search trails the applied one while placeholder rows are up", () => {
  const first: RowSearch = { column: "title", term: "abc" }
  const second: RowSearch = { column: "title", term: "abcd" }
  const { result, rerender } = renderHook(
    ({ applied, isPlaceholderData }) =>
      useShownRowSearch(applied, isPlaceholderData),
    { initialProps: { applied: first, isPlaceholderData: false } }
  )
  expect(result.current).toBe(first)

  rerender({ applied: second, isPlaceholderData: true })
  expect(result.current).toBe(first)

  rerender({ applied: second, isPlaceholderData: false })
  expect(result.current).toBe(second)
})

test("the empty overlay names the applied filter, not the one being typed", () => {
  const query: TableRowQuery = {
    sort: { orderBy: null, sort: null },
    onSortChange: jest.fn(),
    filter: { column: "title", term: "abcd" },
    appliedFilter: { column: "title", term: "abc" },
    onFilterChange: jest.fn(),
  }
  const { rerender } = render(
    <TableRowQueryContext.Provider value={query}>
      <FilteredRowsEmptyOverlay />
    </TableRowQueryContext.Provider>
  )
  expect(screen.getByText(/No rows match “abc” in title/)).toBeInTheDocument()

  // Typed but not yet searched: the empty grid is not the filter's doing.
  rerender(
    <TableRowQueryContext.Provider
      value={{ ...query, appliedFilter: { column: null, term: "" } }}
    >
      <FilteredRowsEmptyOverlay />
    </TableRowQueryContext.Provider>
  )
  expect(screen.getByText("No rows")).toBeInTheDocument()
})
