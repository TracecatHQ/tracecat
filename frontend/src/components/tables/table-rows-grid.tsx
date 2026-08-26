"use client"

import "./ag-grid-setup"

import type {
  ColumnResizedEvent,
  GridApi,
  GridReadyEvent,
  IRowNode,
  RowClassRules,
  RowSelectionOptions,
  SelectionChangedEvent,
  SelectionColumnDef,
} from "ag-grid-community"
import { AgGridReact } from "ag-grid-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { TableColumnRead, TableRowRead } from "@/client"
import { handleGridKeyDown } from "@/components/tables/ag-grid-clipboard"
import { buildReadOnlyColumnDefs } from "@/components/tables/ag-grid-column-defs"
import {
  isUserSelectionSource,
  reconcileSelection,
} from "@/components/tables/ag-grid-selection"
import { tracecatTheme } from "@/components/tables/ag-grid-theme"
import { useLocalStorage } from "@/hooks/use-local-storage"

const MULTI_ROW_SELECTION = {
  mode: "multiRow",
  enableClickSelection: false,
  headerCheckbox: true,
  checkboxes: true,
} as const satisfies RowSelectionOptions<TableRowRead>

const SELECTION_COLUMN_DEF: SelectionColumnDef = {
  cellClass: "ag-selection-col-aligned",
  headerClass: "ag-selection-col-aligned",
}

const EMPTY_SELECTION: ReadonlySet<string> = new Set()

/** Props for {@link TableRowsGrid}. */
export interface TableRowsGridProps {
  /** Schema of the table the rows belong to. */
  columns: readonly TableColumnRead[]
  /** Rows for the current page. Pass a stable reference. */
  rows: readonly TableRowRead[]
  /** Scopes persisted column widths. */
  tableId: string
  /** Renders the grid's built-in loading overlay. */
  isLoading?: boolean
  /** Adds the multi-row checkbox column. */
  selectable?: boolean
  /** Controlled selection by `TableRowRead.id`. May span pages. */
  selectedRowIds?: ReadonlySet<string>
  /** Fires only on user-driven changes, with the full reconciled selection. */
  onSelectedRowIdsChange?: (rowIds: string[]) => void
  /** Sizes the grid to its content instead of filling the parent. */
  autoHeight?: boolean
  /** Conditional row classes, keyed by class name. */
  rowClassRules?: RowClassRules<TableRowRead>
  /** Separates persisted column widths per surface. */
  widthScope?: string
}

/**
 * Presentational grid over externally supplied rows: read-only cells, optional
 * checkbox selection that survives page changes, no route or context coupling.
 * Fetching, pagination and selection state belong to the caller.
 */
export function TableRowsGrid({
  columns,
  rows,
  tableId,
  isLoading,
  selectable = false,
  selectedRowIds,
  onSelectedRowIdsChange,
  autoHeight = false,
  rowClassRules,
  widthScope,
}: TableRowsGridProps) {
  const [gridApi, setGridApi] = useState<GridApi<TableRowRead> | null>(null)
  const [savedWidths, setSavedWidths] = useLocalStorage<Record<string, number>>(
    widthScope
      ? `ag-grid-col-widths:${widthScope}:${tableId}`
      : `ag-grid-col-widths:${tableId}`,
    {}
  )

  // Grid callbacks are registered once, so read the live selection from a ref.
  const selectedRowIdsRef = useRef<ReadonlySet<string>>(
    selectedRowIds ?? EMPTY_SELECTION
  )
  selectedRowIdsRef.current = selectedRowIds ?? EMPTY_SELECTION

  const columnDefs = useMemo(
    () => buildReadOnlyColumnDefs(columns, savedWidths),
    [columns, savedWidths]
  )

  const applySelection = useCallback(
    (api: GridApi<TableRowRead>) => {
      if (!selectable) return
      const selected = selectedRowIdsRef.current
      const toSelect: IRowNode<TableRowRead>[] = []
      const toDeselect: IRowNode<TableRowRead>[] = []
      api.forEachNode((node) => {
        const rowId = node.data?.id
        if (!rowId) return
        const shouldSelect = selected.has(rowId)
        if (shouldSelect === node.isSelected()) return
        if (shouldSelect) {
          toSelect.push(node)
        } else {
          toDeselect.push(node)
        }
      })
      if (toSelect.length > 0) {
        api.setNodesSelected({ nodes: toSelect, newValue: true, source: "api" })
      }
      if (toDeselect.length > 0) {
        api.setNodesSelected({
          nodes: toDeselect,
          newValue: false,
          source: "api",
        })
      }
    },
    [selectable]
  )

  const handleGridReady = useCallback((event: GridReadyEvent<TableRowRead>) => {
    setGridApi(event.api)
  }, [])

  // Re-apply an externally driven selection change even without new row data.
  useEffect(() => {
    if (!gridApi) return
    applySelection(gridApi)
  }, [gridApi, applySelection, selectedRowIds])

  const handleColumnResized = useCallback(
    (event: ColumnResizedEvent<TableRowRead>) => {
      if (!event.finished || !event.api) return
      const widths: Record<string, number> = {}
      for (const col of event.api.getColumns() ?? []) {
        widths[col.getColId()] = col.getActualWidth()
      }
      setSavedWidths(widths)
    },
    [setSavedWidths]
  )

  const handleSelectionChanged = useCallback(
    (event: SelectionChangedEvent<TableRowRead>) => {
      if (!selectable || !onSelectedRowIdsChange) return
      if (!isUserSelectionSource(event.source)) return
      const visibleIds: string[] = []
      event.api.forEachNode((node) => {
        const rowId = node.data?.id
        if (rowId) visibleIds.push(rowId)
      })
      const selectedVisibleIds = event.api
        .getSelectedRows()
        .map((row) => row.id)
      onSelectedRowIdsChange(
        reconcileSelection({
          previous: selectedRowIdsRef.current,
          visibleIds,
          selectedVisibleIds,
        })
      )
    },
    [selectable, onSelectedRowIdsChange]
  )

  return (
    <div
      className={autoHeight ? "" : "h-full"}
      onKeyDown={(e) => handleGridKeyDown(e, gridApi)}
    >
      <AgGridReact<TableRowRead>
        theme={tracecatTheme}
        domLayout={autoHeight ? "autoHeight" : undefined}
        rowData={rows as TableRowRead[]}
        columnDefs={columnDefs}
        rowClassRules={rowClassRules}
        getRowId={(params) => params.data.id}
        onGridReady={handleGridReady}
        onColumnResized={handleColumnResized}
        onFirstDataRendered={(event) => applySelection(event.api)}
        onRowDataUpdated={(event) => applySelection(event.api)}
        onSelectionChanged={handleSelectionChanged}
        rowSelection={selectable ? MULTI_ROW_SELECTION : undefined}
        selectionColumnDef={selectable ? SELECTION_COLUMN_DEF : undefined}
        suppressContextMenu
        headerHeight={36}
        rowHeight={36}
        animateRows={false}
        loading={isLoading}
      />
    </div>
  )
}
