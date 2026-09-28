"use client"

import "./ag-grid-setup"

import type {
  CellValueChangedEvent,
  ColDef,
  ColumnResizedEvent,
  GridApi,
  GridReadyEvent,
  SelectionChangedEvent,
} from "ag-grid-community"
import { AgGridReact } from "ag-grid-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { TableRead, TableRowRead } from "@/client"
import { AgGridCellEditor } from "@/components/tables/ag-grid-cell-editor"
import { AgGridCellRenderer } from "@/components/tables/ag-grid-cell-renderer"
import { handleGridKeyDown } from "@/components/tables/ag-grid-clipboard"
import {
  buildBaseColumnDef,
  isJsonColumn,
  suppressEditorKeys,
} from "@/components/tables/ag-grid-column-defs"
import { AgGridColumnHeader } from "@/components/tables/ag-grid-column-header"
import { AgGridContextMenu } from "@/components/tables/ag-grid-context-menu"
import { AgGridPagination } from "@/components/tables/ag-grid-pagination"
import { tracecatTheme } from "@/components/tables/ag-grid-theme"
import { useTableSelection } from "@/components/tables/table-selection-context"
import { useTablesPagination } from "@/hooks/pagination/use-tables-pagination"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { useUpdateRow } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Editable grid for a table's rows on the tables route. */
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

  const {
    data: rows,
    isLoading,
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
  } = useTablesPagination({
    tableId: id,
    workspaceId,
    limit: pageSize,
  })
  const rowData = rows ?? []

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

  const columnDefs: ColDef[] = useMemo(() => {
    const defs: ColDef[] = [
      ...columns.map((column): ColDef => {
        const baseDef = buildBaseColumnDef(column, savedWidths)

        return {
          ...baseDef,
          headerComponent: AgGridColumnHeader,
          headerComponentParams: {
            tableColumn: column,
          },
          cellRenderer: AgGridCellRenderer,
          cellRendererParams: {
            tableColumn: column,
          },
          // JSON columns are edited only via the side panel
          ...(isJsonColumn(column)
            ? { editable: false }
            : {
                cellEditor: AgGridCellEditor,
                cellEditorParams: { tableColumn: column },
                suppressKeyboardEvent: suppressEditorKeys,
                editable: true,
              }),
        }
      }),
    ]
    return defs
  }, [columns, savedWidths])

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-destructive">
          Failed to load table rows. Please try refreshing the page.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 pb-2 h-full">
      <AgGridContextMenu gridApi={gridApi} columns={columns}>
        <div
          className="flex-1 min-h-0"
          onKeyDown={(e) => handleGridKeyDown(e, gridApi)}
        >
          <AgGridReact
            theme={tracecatTheme}
            rowData={rowData}
            columnDefs={columnDefs}
            getRowId={(params) => params.data.id}
            onGridReady={handleGridReady}
            onColumnResized={handleColumnResized}
            onCellValueChanged={handleCellValueChanged}
            onSelectionChanged={handleSelectionChanged}
            selectionColumnDef={{
              cellClass: "ag-selection-col-aligned",
              headerClass: "ag-selection-col-aligned",
            }}
            rowSelection={{
              mode: "multiRow",
              enableClickSelection: false,
              headerCheckbox: true,
              checkboxes: true,
            }}
            suppressContextMenu
            headerHeight={36}
            rowHeight={36}
            animateRows={false}
            loading={isLoading}
          />
        </div>
      </AgGridContextMenu>
      <AgGridPagination
        currentPage={currentPage}
        hasNextPage={hasNextPage}
        hasPreviousPage={hasPreviousPage}
        pageSize={pageSize}
        totalEstimate={totalEstimate}
        startItem={startItem}
        endItem={endItem}
        onNextPage={goToNextPage}
        onPreviousPage={goToPreviousPage}
        onFirstPage={goToFirstPage}
        onPageSizeChange={handlePageSizeChange}
        isLoading={isLoading}
      />
    </div>
  )
}
