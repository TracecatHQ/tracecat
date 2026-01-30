"use client"

import "./ag-grid-setup"

import type {
  CellValueChangedEvent,
  ColDef,
  GridApi,
  GridReadyEvent,
} from "ag-grid-community"
import { AgGridReact } from "ag-grid-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { TableRead, TableRowRead } from "@/client"
import { AgGridCellEditor } from "@/components/tables/ag-grid-cell-editor"
import { AgGridCellRenderer } from "@/components/tables/ag-grid-cell-renderer"
import { handleGridKeyDown } from "@/components/tables/ag-grid-clipboard"
import { AgGridColumnHeader } from "@/components/tables/ag-grid-column-header"
import { AgGridContextMenu } from "@/components/tables/ag-grid-context-menu"
import { AgGridPagination } from "@/components/tables/ag-grid-pagination"
import { tracecatTheme } from "@/components/tables/ag-grid-theme"
import { useTablesPagination } from "@/hooks/pagination/use-tables-pagination"
import { useUpdateRow } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const TEXT_TYPES = new Set([
  "TEXT",
  "VARCHAR",
  "CHAR",
  "CITEXT",
  "UUID",
  "BPCHAR",
])
const JSON_TYPES = new Set(["JSON", "JSONB"])
const NUMERIC_TYPES = new Set([
  "INT",
  "INTEGER",
  "BIGINT",
  "SMALLINT",
  "DECIMAL",
  "NUMERIC",
  "REAL",
  "FLOAT",
  "FLOAT8",
  "FLOAT4",
  "DOUBLE",
  "DOUBLE PRECISION",
  "BIGSERIAL",
  "SERIAL",
  "SERIAL4",
  "SERIAL8",
])
const DATE_TYPES = new Set([
  "DATE",
  "TIMESTAMP",
  "TIMESTAMPTZ",
  "TIME",
  "TIMETZ",
])
const BOOLEAN_TYPES = new Set(["BOOL", "BOOLEAN"])
const POPUP_EDITOR_TYPES = new Set([
  "TEXT",
  "VARCHAR",
  "CHAR",
  "CITEXT",
  "BPCHAR",
  "TIMESTAMP",
  "TIMESTAMPTZ",
  "MULTI_SELECT",
])

function normalizeSqlType(rawType?: string) {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

function getColumnWidthPx(rawType?: string): number {
  const normalizedType = normalizeSqlType(rawType)
  if (JSON_TYPES.has(normalizedType)) return 480
  if (TEXT_TYPES.has(normalizedType)) return 384
  if (DATE_TYPES.has(normalizedType)) return 288
  if (BOOLEAN_TYPES.has(normalizedType)) return 160
  if (NUMERIC_TYPES.has(normalizedType)) return 224
  return 288
}

export function AgGridTable({
  table: { id, name, columns },
}: {
  table: TableRead
}) {
  const workspaceId = useWorkspaceId()
  const [pageSize, setPageSize] = useState(20)
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  const { updateRow } = useUpdateRow()

  const {
    data: rows,
    isLoading: rowsIsLoading,
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

  const handleGridReady = useCallback((event: GridReadyEvent) => {
    setGridApi(event.api)
  }, [])

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

  const columnDefs: ColDef[] = useMemo(() => {
    const defs: ColDef[] = [
      ...columns.map((column): ColDef => {
        const normalizedType = normalizeSqlType(column.type)
        const isPopupEditor = POPUP_EDITOR_TYPES.has(normalizedType)

        return {
          field: column.name,
          headerName: column.name,
          headerComponent: AgGridColumnHeader,
          headerComponentParams: {
            tableColumn: column,
          },
          cellRenderer: AgGridCellRenderer,
          cellRendererParams: {
            tableColumn: column,
          },
          cellEditor: AgGridCellEditor,
          cellEditorParams: {
            tableColumn: column,
          },
          cellEditorPopup: isPopupEditor,
          editable: true,
          sortable: true,
          resizable: true,
          width: getColumnWidthPx(column.type),
          minWidth: 100,
        }
      }),
    ]
    return defs
  }, [columns])

  return (
    <div className="flex h-full flex-col gap-2">
      <AgGridContextMenu gridApi={gridApi} columns={columns}>
        <div onKeyDown={(e) => handleGridKeyDown(e, gridApi)}>
          <AgGridReact
            theme={tracecatTheme}
            domLayout="autoHeight"
            rowData={rows ?? []}
            columnDefs={columnDefs}
            getRowId={(params) => params.data.id}
            onGridReady={handleGridReady}
            onCellValueChanged={handleCellValueChanged}
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
            suppressClickEdit
            suppressContextMenu
            headerHeight={36}
            rowHeight={36}
            animateRows={false}
            loading={rowsIsLoading}
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
        isLoading={rowsIsLoading}
      />
    </div>
  )
}
