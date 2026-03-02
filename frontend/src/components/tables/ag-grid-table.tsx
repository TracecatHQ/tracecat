"use client"

import "./ag-grid-setup"

import type {
  CellValueChangedEvent,
  ColDef,
  GridApi,
  GridReadyEvent,
  SelectionChangedEvent,
  SuppressKeyboardEventParams,
  ValueFormatterParams,
} from "ag-grid-community"
import { AgGridReact } from "ag-grid-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { TableColumnRead, TableRead, TableRowRead } from "@/client"
import { AgGridCellEditor } from "@/components/tables/ag-grid-cell-editor"
import { AgGridCellRenderer } from "@/components/tables/ag-grid-cell-renderer"
import { handleGridKeyDown } from "@/components/tables/ag-grid-clipboard"
import { AgGridColumnHeader } from "@/components/tables/ag-grid-column-header"
import { AgGridContextMenu } from "@/components/tables/ag-grid-context-menu"
import { AgGridPagination } from "@/components/tables/ag-grid-pagination"
import { tracecatTheme } from "@/components/tables/ag-grid-theme"
import { CellDisplay } from "@/components/tables/cell-display"
import { useTableSelection } from "@/components/tables/table-selection-context"
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
const POPUP_EDITOR_TYPES = new Set(["JSON", "JSONB"])

function normalizeSqlType(rawType?: string) {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

/**
 * Suppress Enter, Tab, and Escape during editing so the cell editor
 * handles commit/cancel exclusively, preventing AG Grid from calling
 * getValue() before the editor has called onChange with the parsed value.
 */
function suppressEditorKeys(params: SuppressKeyboardEventParams): boolean {
  if (!params.editing) return false
  const key = params.event.key
  return key === "Enter" || key === "Tab" || key === "Escape"
}

function numericValueFormatter(params: ValueFormatterParams): string {
  const value = params.value
  if (value === null || value === undefined) return ""
  if (typeof value !== "number") return String(value)
  if (!Number.isFinite(value)) return String(value)
  if (Number.isInteger(value)) return String(value)
  return parseFloat(value.toFixed(2)).toString()
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

function ReadOnlyCellRenderer({
  value,
  tableColumn,
}: {
  value: unknown
  tableColumn: TableColumnRead
}) {
  return (
    <div className="flex h-full w-full items-center overflow-hidden">
      <div className="min-w-0 flex-1 overflow-hidden">
        <CellDisplay value={value} column={tableColumn} />
      </div>
    </div>
  )
}

export function AgGridTable({
  table: { id, name, columns },
  rowsOverride,
  readOnly = false,
  hidePagination = false,
}: {
  table: TableRead
  rowsOverride?: TableRowRead[]
  readOnly?: boolean
  hidePagination?: boolean
}) {
  const isReadOnly = readOnly || rowsOverride !== undefined
  const workspaceId = useWorkspaceId()
  const [pageSize, setPageSize] = useState(20)
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  const { updateRow } = useUpdateRow()
  const { updateSelection } = useTableSelection()

  const {
    data: rows,
    isLoading: rowsIsLoading,
    error: rowsError,
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
    enabled: rowsOverride === undefined,
  })
  const rowData = rowsOverride ?? rows ?? []
  const isLoading = rowsOverride === undefined ? rowsIsLoading : false
  const error = rowsOverride === undefined ? rowsError : null

  useEffect(() => {
    if (id && rowsOverride === undefined) {
      document.title = `Tables | ${name}`
    }
  }, [id, name, rowsOverride])

  const handlePageSizeChange = useCallback(
    (newPageSize: number) => {
      if (rowsOverride !== undefined) {
        return
      }
      setPageSize(newPageSize)
      goToFirstPage()
    },
    [goToFirstPage, rowsOverride]
  )

  const handleGridReady = useCallback(
    (event: GridReadyEvent) => {
      setGridApi(event.api)
      if (isReadOnly) {
        return
      }
      updateSelection({
        gridApi: event.api,
        tableId: id,
        columns,
        selectedCount: 0,
        selectedRowIds: [],
      })
    },
    [updateSelection, id, columns, isReadOnly]
  )

  // Keep selection context in sync when table id or columns change after grid init
  useEffect(() => {
    if (isReadOnly) {
      return
    }
    if (gridApi) {
      updateSelection({
        tableId: id,
        columns,
        selectedCount: 0,
        selectedRowIds: [],
      })
      gridApi.deselectAll()
    }
  }, [id, columns, gridApi, updateSelection, isReadOnly])

  const handleSelectionChanged = useCallback(
    (event: SelectionChangedEvent) => {
      if (isReadOnly) {
        return
      }
      const selectedRows = event.api.getSelectedRows() as TableRowRead[]
      updateSelection({
        selectedCount: selectedRows.length,
        selectedRowIds: selectedRows.map((r) => r.id),
      })
    },
    [updateSelection, isReadOnly]
  )

  const handleCellValueChanged = useCallback(
    (event: CellValueChangedEvent) => {
      if (isReadOnly) {
        return
      }
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
    [id, workspaceId, updateRow, isReadOnly]
  )

  const columnDefs: ColDef[] = useMemo(() => {
    const defs: ColDef[] = [
      ...columns.map((column): ColDef => {
        const normalizedType = normalizeSqlType(column.type)
        const isPopupEditor = POPUP_EDITOR_TYPES.has(normalizedType)
        const isNumeric = NUMERIC_TYPES.has(normalizedType)
        const baseDef: ColDef = {
          field: column.name,
          headerName: column.name,
          sortable: true,
          resizable: true,
          width: getColumnWidthPx(column.type),
          minWidth: 100,
          ...(isNumeric && { valueFormatter: numericValueFormatter }),
        }

        if (isReadOnly) {
          return {
            ...baseDef,
            cellRenderer: ReadOnlyCellRenderer,
            cellRendererParams: {
              tableColumn: column,
            },
            editable: false,
          }
        }

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
          cellEditor: AgGridCellEditor,
          cellEditorParams: {
            tableColumn: column,
          },
          cellEditorPopup: isPopupEditor,
          suppressKeyboardEvent: suppressEditorKeys,
          editable: true,
        }
      }),
    ]
    return defs
  }, [columns, isReadOnly])

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
    <div className="flex h-full flex-col gap-2">
      {isReadOnly ? (
        <div onKeyDown={(e) => handleGridKeyDown(e, gridApi)}>
          <AgGridReact
            theme={tracecatTheme}
            domLayout="autoHeight"
            rowData={rowData}
            columnDefs={columnDefs}
            getRowId={(params) => params.data.id}
            onGridReady={handleGridReady}
            suppressContextMenu
            headerHeight={36}
            rowHeight={36}
            animateRows={false}
            loading={isLoading}
          />
        </div>
      ) : (
        <AgGridContextMenu gridApi={gridApi} columns={columns}>
          <div onKeyDown={(e) => handleGridKeyDown(e, gridApi)}>
            <AgGridReact
              theme={tracecatTheme}
              domLayout="autoHeight"
              rowData={rowData}
              columnDefs={columnDefs}
              getRowId={(params) => params.data.id}
              onGridReady={handleGridReady}
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
              suppressClickEdit
              suppressContextMenu
              headerHeight={36}
              rowHeight={36}
              animateRows={false}
              loading={isLoading}
            />
          </div>
        </AgGridContextMenu>
      )}
      {!hidePagination && rowsOverride === undefined && (
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
      )}
    </div>
  )
}
