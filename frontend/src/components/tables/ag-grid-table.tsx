"use client"

import "./ag-grid-setup"

import type {
  CellValueChangedEvent,
  ColDef,
  ColumnResizedEvent,
  GetRowIdParams,
  GridApi,
  GridReadyEvent,
  RowSelectionOptions,
  SelectionChangedEvent,
  SelectionColumnDef,
} from "ag-grid-community"
import { AgGridReact } from "ag-grid-react"
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react"
import type { TableRead, TableRowRead } from "@/client"
import { handleGridKeyDown } from "@/components/tables/ag-grid-clipboard"
import {
  buildEditableColumnDef,
  isSearchableColumn,
} from "@/components/tables/ag-grid-column-defs"
import { AgGridColumnHeader } from "@/components/tables/ag-grid-column-header"
import { AgGridContextMenu } from "@/components/tables/ag-grid-context-menu"
import { AgGridPagination } from "@/components/tables/ag-grid-pagination"
import { tracecatTheme } from "@/components/tables/ag-grid-theme"
import {
  type TableRowQuery,
  TableRowQueryContext,
} from "@/components/tables/table-row-query-context"
import { TableRowSearchBar } from "@/components/tables/table-row-search-bar"
import { useTableSelection } from "@/components/tables/table-selection-context"
import { useTablesPagination } from "@/hooks/pagination/use-tables-pagination"
import { useLocalStorage } from "@/hooks/use-local-storage"
import {
  toRowSearchParams,
  useRowSearch,
  useStaleRowQueryReset,
} from "@/hooks/use-row-search"
import { useUpdateRow } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

// Stable references: the grid re-applies any option whose identity changes,
// and this component renders on every keystroke of the search.
const EMPTY_ROWS: TableRowRead[] = []

const MULTI_ROW_SELECTION: RowSelectionOptions = {
  mode: "multiRow",
  enableClickSelection: false,
  headerCheckbox: true,
  checkboxes: true,
}

const SELECTION_COLUMN_DEF: SelectionColumnDef = {
  cellClass: "ag-selection-col-aligned",
  headerClass: "ag-selection-col-aligned",
}

function getRowId(params: GetRowIdParams<TableRowRead>): string {
  return params.data.id
}

/**
 * Editable grid for a table's rows on the tables route, under a search row.
 * Search and sort are both run by the API across the whole table.
 */
export function AgGridTable({
  table: { id, name, columns },
}: {
  table: TableRead
}) {
  const workspaceId = useWorkspaceId()
  const [pageSize, setPageSize] = useState(20)
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  const { updateRow } = useUpdateRow()
  const { updateSelection } = useTableSelection()
  const [savedWidths, setSavedWidths] = useLocalStorage<Record<string, number>>(
    `ag-grid-col-widths:${id}`,
    {}
  )

  const searchableColumns = useMemo(
    () => columns.filter(isSearchableColumn),
    [columns]
  )
  const { search, setSearch, debouncedSearch, clearSearch } = useRowSearch()
  // Until one is picked, or once the picked one is gone, search the first.
  function resolveSearchColumn(name: string | null): string | null {
    const picked = searchableColumns.find((column) => column.name === name)
    return (picked ?? searchableColumns[0])?.name ?? null
  }
  const { searchTerm, searchColumn } = toRowSearchParams({
    term: debouncedSearch.term,
    column: resolveSearchColumn(debouncedSearch.column),
  })

  const {
    data: rows,
    isLoading,
    isPlaceholderData,
    error,
    goToNextPage,
    goToPreviousPage,
    goToFirstPage,
    hasNextPage,
    hasPreviousPage,
    currentPage,
    totalEstimate,
    startItem,
    endItem,
    sortingState,
    setSorting,
  } = useTablesPagination({
    tableId: id,
    workspaceId,
    limit: pageSize,
    searchTerm,
    searchColumn,
  })
  const rowData = rows.length > 0 ? rows : EMPTY_ROWS
  const isSearching = searchTerm !== null
  // The API's total counts the whole table, so it is wrong during a search.
  const totalRows = isSearching ? undefined : totalEstimate

  const rowQuery = useMemo<TableRowQuery>(
    () => ({ sort: sortingState, onSortChange: setSorting }),
    [sortingState, setSorting]
  )
  const resetRowQuery = useCallback(() => {
    setSorting("", false)
    clearSearch()
  }, [setSorting, clearSearch])
  const isStaleRowQuery = useStaleRowQueryReset({
    error,
    isActive: isSearching || sortingState.orderBy !== null,
    reset: resetRowQuery,
  })
  // Deleting or renaming the sorted column removes the only header that could
  // clear its sort, and no request fails to trigger the reset above.
  const sortedColumn = sortingState.orderBy
  useEffect(() => {
    if (
      sortedColumn !== null &&
      !columns.some((column) => column.name === sortedColumn)
    ) {
      setSorting("", false)
    }
  }, [columns, sortedColumn, setSorting])
  // With no searchable column left the input is disabled, so a typed term
  // could not be cleared and would later apply to a newly added column.
  const hasSearchableColumn = searchableColumns.length > 0
  const hasSearchTerm = search.term !== ""
  useEffect(() => {
    if (!hasSearchableColumn && hasSearchTerm) {
      clearSearch()
    }
  }, [hasSearchableColumn, hasSearchTerm, clearSearch])

  useEffect(() => {
    if (id) {
      document.title = `Tables | ${name}`
    }
  }, [id, name])

  const handlePageSizeChange = useCallback(
    (newPageSize: number) => {
      setPageSize(newPageSize)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handleGridReady = useCallback(
    (event: GridReadyEvent) => {
      setGridApi(event.api)
      updateSelection({
        gridApi: event.api,
        tableId: id,
        columns,
        selectedCount: 0,
        selectedRowIds: [],
      })
    },
    [updateSelection, id, columns]
  )

  // Keep selection context in sync when table id or columns change after grid init
  useEffect(() => {
    if (gridApi) {
      updateSelection({
        tableId: id,
        columns,
        selectedCount: 0,
        selectedRowIds: [],
      })
      gridApi.deselectAll()
    }
  }, [id, columns, gridApi, updateSelection])

  const handleSelectionChanged = useCallback(
    (event: SelectionChangedEvent) => {
      const selectedRows = event.api.getSelectedRows() as TableRowRead[]
      updateSelection({
        selectedCount: selectedRows.length,
        selectedRowIds: selectedRows.map((r) => r.id),
      })
    },
    [updateSelection]
  )

  const handleCellValueChanged = useCallback(
    (event: CellValueChangedEvent) => {
      if (event.oldValue !== event.newValue && event.colDef.field) {
        const rowData = event.data as TableRowRead
        updateRow({
          tableId: id,
          rowId: rowData.id,
          workspaceId,
          requestBody: {
            data: { [event.colDef.field]: event.newValue },
          },
        })
      }
    },
    [id, workspaceId, updateRow]
  )

  const handleColumnResized = useCallback(
    (event: ColumnResizedEvent) => {
      if (!event.finished || !event.api) return
      const widths: Record<string, number> = {}
      for (const col of event.api.getColumns() ?? []) {
        widths[col.getColId()] = col.getActualWidth()
      }
      setSavedWidths(widths)
    },
    [setSavedWidths]
  )

  const columnDefs: ColDef[] = useMemo(
    () =>
      columns.map(
        (column): ColDef => ({
          ...buildEditableColumnDef(column, savedWidths),
          headerComponent: AgGridColumnHeader,
          headerComponentParams: {
            tableColumn: column,
          },
        })
      ),
    [columns, savedWidths]
  )

  let gridContent: ReactNode
  if (error && !isStaleRowQuery) {
    gridContent = (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-destructive">
          Failed to load table rows. Please try refreshing the page.
        </p>
      </div>
    )
  } else {
    gridContent = (
      <AgGridContextMenu gridApi={gridApi} columns={columns}>
        <div
          // Dimmed while the previous rows stand in for the next request's.
          className={cn(
            "h-full transition-opacity",
            isPlaceholderData && "opacity-60"
          )}
          aria-busy={isPlaceholderData}
          onKeyDown={(e) => handleGridKeyDown(e, gridApi)}
        >
          <TableRowQueryContext.Provider value={rowQuery}>
            <AgGridReact
              theme={tracecatTheme}
              rowData={rowData}
              columnDefs={columnDefs}
              getRowId={getRowId}
              onGridReady={handleGridReady}
              onColumnResized={handleColumnResized}
              onCellValueChanged={handleCellValueChanged}
              onSelectionChanged={handleSelectionChanged}
              selectionColumnDef={SELECTION_COLUMN_DEF}
              rowSelection={MULTI_ROW_SELECTION}
              suppressContextMenu
              headerHeight={36}
              rowHeight={36}
              animateRows={false}
              loading={isLoading}
            />
          </TableRowQueryContext.Provider>
        </div>
      </AgGridContextMenu>
    )
  }

  return (
    <div className="flex h-full flex-col pb-2">
      <TableRowSearchBar
        searchableColumns={searchableColumns}
        term={search.term}
        onTermChange={(term) => setSearch({ ...search, term })}
        column={resolveSearchColumn(search.column)}
        onColumnChange={(column) => setSearch({ ...search, column })}
        rowCount={totalRows}
      />
      <div className="min-h-0 flex-1">{gridContent}</div>
      <div className="pt-2">
        <AgGridPagination
          currentPage={currentPage}
          hasNextPage={hasNextPage}
          hasPreviousPage={hasPreviousPage}
          pageSize={pageSize}
          totalEstimate={totalRows}
          startItem={startItem}
          endItem={endItem}
          onNextPage={goToNextPage}
          onPreviousPage={goToPreviousPage}
          onFirstPage={goToFirstPage}
          onPageSizeChange={handlePageSizeChange}
          // The placeholder rows' cursors belong to the previous request.
          isLoading={isLoading || isPlaceholderData}
        />
      </div>
    </div>
  )
}
