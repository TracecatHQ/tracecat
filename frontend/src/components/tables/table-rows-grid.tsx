"use client"

import "./ag-grid-setup"

import type {
  CellValueChangedEvent,
  ColDef,
  ColumnResizedEvent,
  GetRowIdParams,
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
import { FilteredRowsEmptyOverlay } from "@/components/tables/column-header-filter"
import {
  type TableRowQuery,
  TableRowQueryContext,
} from "@/components/tables/table-row-query-context"
import { TableRowsGridHeader } from "@/components/tables/table-rows-grid-header"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { cn } from "@/lib/utils"

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

function getRowId(params: GetRowIdParams<TableRowRead>): string {
  return params.data.id
}

/** Breathing room added to each measured column, on top of the cell padding. */
const CONTENT_SIZED_PADDING_PX = 8

/**
 * Whether a content-sized grid has something real to measure: a laid-out
 * width, no load in flight, and `rowCount` rows on screen with their cells
 * filled in. React cell renderers land a commit after their cells do, so an
 * empty cell means the grid is still rendering.
 */
export function isReadyToMeasure({
  wrapper,
  isLoading,
  rowCount,
}: {
  wrapper: HTMLElement | null
  isLoading: boolean
  rowCount: number
}): boolean {
  if (!wrapper || wrapper.clientWidth <= 0 || isLoading) return false
  if (rowCount === 0) return true
  const renderedRows = wrapper.querySelectorAll(
    ".ag-center-cols-container .ag-row"
  )
  if (renderedRows.length < rowCount) return false
  for (const cell of wrapper.querySelectorAll(
    ".ag-center-cols-container .ag-cell"
  )) {
    if (cell.childElementCount === 0) return false
  }
  return true
}

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
  /**
   * Marks `rows` as the previous request's, kept on screen while the next
   * loads: the grid is dimmed, not emptied.
   */
  isPlaceholderData?: boolean
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
  /**
   * Gives every column header a sort button, and a filter button on columns
   * the API can search when `onFilterChange` is set. The grid never sorts or
   * filters rows itself: it reports the request and shows the `rows` it is
   * handed, so the caller must fetch them accordingly. Pass a memoized value.
   */
  rowQuery?: TableRowQuery
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
  isPlaceholderData = false,
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
  rowQuery,
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

  const hasRowQuery = rowQuery !== undefined
  const columnDefs = useMemo(() => {
    let defs: ColDef<TableRowRead>[]
    if (cellPanel) {
      defs = columns.map(
        (column): ColDef<TableRowRead> => ({
          ...buildEditableColumnDef(column, savedWidths, {
            canEditRow,
            reserveButtonSpace: sizeColumnsToContent,
          }),
        })
      )
    } else {
      defs = buildReadOnlyColumnDefs(columns, savedWidths)
    }
    if (hasRowQuery) {
      // Both builders return one def per column, in order. The headers read
      // the live query from context, so the defs do not depend on it.
      defs = defs.map((def, index) => ({
        ...def,
        headerComponent: TableRowsGridHeader,
        headerComponentParams: { tableColumn: columns[index] },
      }))
    }
    if (!sizeColumnsToContent) return defs
    return defs.map((def) =>
      toContentSizedColumnDef(
        def,
        def.field ? savedWidths[def.field] : undefined
      )
    )
  }, [
    cellPanel,
    columns,
    savedWidths,
    canEditRow,
    sizeColumnsToContent,
    hasRowQuery,
  ])

  // -- Content sizing ------------------------------------------------------
  // Two steps, both the grid's own: `autoSizeColumns` measures the columns the
  // user has not dragged, then `sizeColumnsToFit` shares out what is left with
  // each measured width as that column's floor.
  //
  // A measurement is only worth taking once there is something to measure, so
  // a request waits until the grid has a width and the rows it was asked about
  // are on screen with their cells rendered. Measuring is also asynchronous,
  // and the grid reports each one through the same event, so requests are
  // counted: only the answer to the last one outstanding is kept. Taking an
  // earlier answer (headers alone, from before the rows arrived) as the
  // measurement is what once squeezed every column to a near-equal share.
  const wrapperRef = useRef<HTMLDivElement>(null)
  const measuredWidthsRef = useRef<Map<string, number>>(new Map())
  const needsMeasureRef = useRef(false)
  const pendingMeasuresRef = useRef(0)
  const lastGridWidthRef = useRef(0)
  const isLoadingRef = useRef(isLoading)
  isLoadingRef.current = isLoading
  const rowCountRef = useRef(rows.length)
  rowCountRef.current = rows.length

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

  /** Measures if a measurement is wanted and the grid is ready to give one. */
  const measureWhenReady = useCallback((api: GridApi<TableRowRead>) => {
    if (!needsMeasureRef.current || api.isDestroyed()) return
    // A tall page is virtualised, so expect the rows the grid means to draw,
    // not every row it was handed. None yet means it has not taken them in.
    const drawnRows = api.getRenderedNodes().length
    if (rowCountRef.current > 0 && drawnRows === 0) return
    const ready = isReadyToMeasure({
      wrapper: wrapperRef.current,
      isLoading: isLoadingRef.current === true,
      rowCount: Math.min(rowCountRef.current, drawnRows),
    })
    if (!ready) return
    needsMeasureRef.current = false
    const saved = savedWidthsRef.current
    const colIds = api
      .getAllDisplayedColumns()
      .filter((column) => column.getColDef().field !== undefined)
      .map((column) => column.getColId())
      .filter((colId) => saved[colId] === undefined)
    if (colIds.length === 0) return
    pendingMeasuresRef.current += 1
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
    needsMeasureRef.current = true
    measureWhenReady(gridApi)
    if (!needsMeasureRef.current) return
    // Not ready yet: the rows, or their React cells, are still on their way
    // into the DOM. Try again as they land, and stop watching once measured.
    const wrapper = wrapperRef.current
    if (!wrapper) return
    const observer = new MutationObserver(() => {
      measureWhenReady(gridApi)
      if (!needsMeasureRef.current) observer.disconnect()
    })
    observer.observe(wrapper, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [
    gridApi,
    sizeColumnsToContent,
    measureWhenReady,
    rowIdsKey,
    columns,
    isLoading,
  ])

  const handleGridSizeChanged = useCallback(
    (event: GridSizeChangedEvent<TableRowRead>) => {
      // Height changes with every row; only a new width moves the columns.
      if (event.clientWidth === lastGridWidthRef.current) return
      lastGridWidthRef.current = event.clientWidth
      if (event.clientWidth <= 0) return
      // A grid that mounted hidden gets its first real width here.
      measureWhenReady(event.api)
      if (needsMeasureRef.current || pendingMeasuresRef.current > 0) return
      fitColumns(event.api)
    },
    [fitColumns, measureWhenReady]
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
        if (
          event.source === "autosizeColumns" &&
          pendingMeasuresRef.current > 0
        ) {
          pendingMeasuresRef.current -= 1
          // An answer to a request since superseded: the next one decides.
          if (pendingMeasuresRef.current > 0) return
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
      ref={wrapperRef}
      className={cn(
        "transition-opacity",
        !autoHeight && "h-full",
        isPlaceholderData && "opacity-60"
      )}
      aria-busy={isPlaceholderData}
      // Copy is always fine; paste only lands in rows whose edits are saved,
      // anywhere else it would fabricate cell values locally.
      onKeyDown={(e) =>
        handleGridKeyDown(e, gridApi, {
          readOnly: !editable,
          canPasteRow: canEditRow,
        })
      }
    >
      <TableRowQueryContext.Provider value={rowQuery ?? null}>
        <AgGridReact<TableRowRead>
          theme={tracecatTheme}
          noRowsOverlayComponent={
            rowQuery?.onFilterChange ? FilteredRowsEmptyOverlay : undefined
          }
          domLayout={autoHeight ? "autoHeight" : undefined}
          rowData={rows as TableRowRead[]}
          columnDefs={columnDefs}
          rowClassRules={rowClassRules}
          getRowId={getRowId}
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
      </TableRowQueryContext.Provider>
    </div>
  )
}
