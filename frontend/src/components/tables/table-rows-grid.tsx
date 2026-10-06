"use client"

import "./ag-grid-setup"

import type {
  CellValueChangedEvent,
  ColDef,
  ColumnResizedEvent,
  GridApi,
  GridReadyEvent,
  GridSizeChangedEvent,
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
import {
  buildEditableColumnDef,
  buildReadOnlyColumnDefs,
  CONTENT_SIZED_MAX_WIDTH_PX,
  CONTENT_SIZED_MIN_WIDTH_PX,
  toContentSizedColumnDef,
} from "@/components/tables/ag-grid-column-defs"
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

/** Breathing room added to each measured column, on top of the cell padding. */
const CONTENT_SIZED_PADDING_PX = 8

/** One committed cell edit reported by an editable {@link TableRowsGrid}. */
export interface TableRowCellChange {
  /** `TableRowRead.id` of the edited row. */
  rowId: string
  /** Name of the edited column. */
  column: string
  /** The value the cell now holds locally, not yet persisted. */
  value: unknown
}

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
  /**
   * Swaps in the tables view's cell renderer, whose hover buttons open long
   * text and JSON in the cell panel. Needs a `TablePanelProvider` above the
   * grid; leave unset anywhere without one.
   */
  cellPanel?: boolean
  /**
   * Makes cells editable, inline and through the cell panel, and reports each
   * committed edit. The grid only changes the value locally: persisting it,
   * and handing back fresh `rows` when that fails, is the caller's job. Only
   * read with `cellPanel`.
   */
  onCellValueChange?: (change: TableRowCellChange) => void
  /**
   * Narrows editing to some rows; omitted means every row. Pass a stable
   * reference, since a new one rebuilds the column defs.
   */
  isRowEditable?: (row: TableRowRead) => boolean
  /**
   * Sizes each column to its header and visible values, between a floor and a
   * cap, then shares any width left over so the grid is never ragged; columns
   * that still do not fit scroll. Measured again when the page of rows changes
   * and re-fitted when the grid's width does, but not on a cell edit.
   *
   * A column the user drags keeps that width. Only those columns are
   * persisted, under their own storage key, so a width the grid chose is never
   * mistaken for one the user did.
   */
  sizeColumnsToContent?: boolean
}

/**
 * Presentational grid over externally supplied rows: read-only cells by
 * default, optional checkbox selection that survives page changes, no route
 * coupling. Fetching, pagination, selection state and persistence belong to
 * the caller.
 *
 * Keyboard copy always works. Paste is off unless the grid is editable,
 * because it would otherwise fabricate cell values that are never written.
 *
 * `cellPanel` and `onCellValueChange` opt in to the tables view's cells; the
 * default grid needs no context above it.
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
  cellPanel = false,
  onCellValueChange,
  isRowEditable,
  sizeColumnsToContent = false,
}: TableRowsGridProps) {
  const [gridApi, setGridApi] = useState<GridApi<TableRowRead> | null>(null)
  const widthStorageScope = widthScope ? `${widthScope}:${tableId}` : tableId
  const [savedWidths, setSavedWidths] = useLocalStorage<Record<string, number>>(
    sizeColumnsToContent
      ? `ag-grid-user-col-widths:${widthStorageScope}`
      : `ag-grid-col-widths:${widthStorageScope}`,
    {}
  )
  const savedWidthsRef = useRef(savedWidths)
  savedWidthsRef.current = savedWidths

  // Grid callbacks are registered once, so read the live selection from a ref.
  const selectedRowIdsRef = useRef<ReadonlySet<string>>(
    selectedRowIds ?? EMPTY_SELECTION
  )
  selectedRowIdsRef.current = selectedRowIds ?? EMPTY_SELECTION

  const editable = cellPanel && onCellValueChange !== undefined
  const onCellValueChangeRef = useRef(onCellValueChange)
  onCellValueChangeRef.current = onCellValueChange

  // Decides inline editing, the renderer's edit buttons and paste alike.
  const canEditRow = useCallback(
    (row: unknown) => {
      if (!editable || !row) return false
      return isRowEditable?.(row as TableRowRead) ?? true
    },
    [editable, isRowEditable]
  )

  const columnDefs = useMemo(() => {
    let defs: ColDef<TableRowRead>[]
    if (cellPanel) {
      defs = columns.map(
        (column): ColDef<TableRowRead> => ({
          ...buildEditableColumnDef(column, savedWidths, {
            canEditRow,
            reserveButtonSpace: sizeColumnsToContent,
          }),
          // One cursor page, as in the read-only defs: no client-side sort.
          sortable: false,
        })
      )
    } else {
      defs = buildReadOnlyColumnDefs(columns, savedWidths)
    }
    if (!sizeColumnsToContent) return defs
    return defs.map((def) =>
      toContentSizedColumnDef(
        def,
        def.field ? savedWidths[def.field] : undefined
      )
    )
  }, [cellPanel, columns, savedWidths, canEditRow, sizeColumnsToContent])

  // -- Content sizing ------------------------------------------------------
  // Two steps, both the grid's own: `autoSizeColumns` measures the columns the
  // user has not dragged, then `sizeColumnsToFit` shares out what is left with
  // each measured width as that column's floor. Measuring is asynchronous (the
  // grid waits for React cells to render), so the fit runs off its event.
  const measuredWidthsRef = useRef<Map<string, number>>(new Map())
  const isMeasuringRef = useRef(false)
  const lastGridWidthRef = useRef(0)

  const fitColumns = useCallback((api: GridApi<TableRowRead>) => {
    if (api.isDestroyed()) return
    const measured = measuredWidthsRef.current
    if (measured.size === 0) return
    const saved = savedWidthsRef.current
    api.sizeColumnsToFit({
      columnLimits: api.getAllDisplayedColumns().map((column) => {
        const colId = column.getColId()
        const measuredWidth =
          saved[colId] === undefined ? measured.get(colId) : undefined
        if (measuredWidth !== undefined) {
          return { key: colId, minWidth: measuredWidth }
        }
        // Dragged columns and the checkbox column stay exactly as they are.
        const width = column.getActualWidth()
        return { key: colId, minWidth: width, maxWidth: width }
      }),
    })
  }, [])

  const measureColumns = useCallback((api: GridApi<TableRowRead>) => {
    if (api.isDestroyed()) return
    const saved = savedWidthsRef.current
    const colIds = api
      .getAllDisplayedColumns()
      .filter((column) => column.getColDef().field !== undefined)
      .map((column) => column.getColId())
      .filter((colId) => saved[colId] === undefined)
    if (colIds.length === 0) return
    isMeasuringRef.current = true
    api.autoSizeColumns({
      colIds,
      defaultMinWidth: CONTENT_SIZED_MIN_WIDTH_PX,
      defaultMaxWidth: CONTENT_SIZED_MAX_WIDTH_PX,
    })
  }, [])

  // The set of rows on screen: a new page is measured, an edited cell is not.
  const rowIdsKey = useMemo(
    () => (sizeColumnsToContent ? rows.map((row) => row.id).join(",") : ""),
    [rows, sizeColumnsToContent]
  )
  useEffect(() => {
    if (!gridApi || !sizeColumnsToContent) return
    // Let the grid take the new rows before it is asked to measure them.
    const frame = requestAnimationFrame(() => measureColumns(gridApi))
    return () => cancelAnimationFrame(frame)
  }, [gridApi, sizeColumnsToContent, measureColumns, rowIdsKey, columns])

  const handleGridSizeChanged = useCallback(
    (event: GridSizeChangedEvent<TableRowRead>) => {
      // Height changes with every row; only a new width moves the columns.
      if (event.clientWidth === lastGridWidthRef.current) return
      lastGridWidthRef.current = event.clientWidth
      if (event.clientWidth <= 0 || isMeasuringRef.current) return
      fitColumns(event.api)
    },
    [fitColumns]
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
      if (sizeColumnsToContent) {
        if (event.source === "autosizeColumns" && isMeasuringRef.current) {
          isMeasuringRef.current = false
          const measured = new Map<string, number>()
          for (const column of event.columns ?? []) {
            measured.set(column.getColId(), column.getActualWidth())
          }
          measuredWidthsRef.current = measured
          fitColumns(event.api)
          return
        }
        // Only a drag is the user's choice; the grid's own sizing is not.
        if (event.source !== "uiColumnResized") return
        const dragged = event.columns ?? []
        if (dragged.length === 0) return
        const widths = { ...savedWidthsRef.current }
        for (const column of dragged) {
          widths[column.getColId()] = column.getActualWidth()
        }
        setSavedWidths(widths)
        return
      }
      const widths: Record<string, number> = {}
      for (const col of event.api.getColumns() ?? []) {
        widths[col.getColId()] = col.getActualWidth()
      }
      setSavedWidths(widths)
    },
    [setSavedWidths, sizeColumnsToContent, fitColumns]
  )

  const handleCellValueChanged = useCallback(
    (event: CellValueChangedEvent<TableRowRead>) => {
      const column = event.colDef.field
      if (!column || event.oldValue === event.newValue) return
      if (!canEditRow(event.data)) return
      onCellValueChangeRef.current?.({
        rowId: event.data.id,
        column,
        value: event.newValue,
      })
    },
    [canEditRow]
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
      // Copy is always fine; paste only lands in rows whose edits are saved,
      // anywhere else it would fabricate cell values locally.
      onKeyDown={(e) =>
        handleGridKeyDown(e, gridApi, {
          readOnly: !editable,
          canPasteRow: canEditRow,
        })
      }
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
        onGridSizeChanged={
          sizeColumnsToContent ? handleGridSizeChanged : undefined
        }
        // Columns scrolled out of view have no cells to measure.
        suppressColumnVirtualisation={sizeColumnsToContent}
        autoSizePadding={
          sizeColumnsToContent ? CONTENT_SIZED_PADDING_PX : undefined
        }
        onCellValueChanged={editable ? handleCellValueChanged : undefined}
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
