"use client"

import type {
  ColDef,
  SuppressKeyboardEventParams,
  ValueFormatterParams,
} from "ag-grid-community"
import type { TableColumnRead } from "@/client"
import { AgGridCellEditor } from "@/components/tables/ag-grid-cell-editor"
import { AgGridCellRenderer } from "@/components/tables/ag-grid-cell-renderer"
import { CellDisplay } from "@/components/tables/cell-display"

/** SQL types rendered as free text. */
export const TEXT_TYPES = new Set([
  "TEXT",
  "VARCHAR",
  "CHAR",
  "CITEXT",
  "BPCHAR",
])
/** SQL types holding structured JSON payloads. */
export const JSON_TYPES = new Set(["JSON", "JSONB"])
/** SQL types rendered and formatted as numbers. */
export const NUMERIC_TYPES = new Set([
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
/** SQL types rendered as dates or timestamps. */
export const DATE_TYPES = new Set(["DATE", "TIMESTAMPTZ", "TIME", "TIMETZ"])
/** SQL types rendered as booleans. */
export const BOOLEAN_TYPES = new Set(["BOOL", "BOOLEAN"])
/** Column types the rows API accepts as a `searchColumn`. */
export const SEARCHABLE_TYPES = new Set([
  "TEXT",
  "SELECT",
  "JSONB",
  "MULTI_SELECT",
])

/** Strip any precision suffix and upper-case a raw SQL type name. */
export function normalizeSqlType(rawType?: string) {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

/** Whether a column holds a JSON payload, which is never inline-editable. */
export function isJsonColumn(column: TableColumnRead): boolean {
  return JSON_TYPES.has(normalizeSqlType(column.type))
}

/** Whether the rows API can run a text search over a column. */
export function isSearchableColumn(column: TableColumnRead): boolean {
  return SEARCHABLE_TYPES.has(normalizeSqlType(column.type))
}

/**
 * Suppress Enter, Tab, and Escape during editing so the cell editor
 * handles commit/cancel exclusively, preventing AG Grid from calling
 * getValue() before the editor has called onChange with the parsed value.
 */
export function suppressEditorKeys(
  params: SuppressKeyboardEventParams
): boolean {
  if (!params.editing) return false
  const key = params.event.key
  return key === "Enter" || key === "Tab" || key === "Escape"
}

/** Render numbers without scientific notation, trimming to four decimals. */
export function numericValueFormatter(params: ValueFormatterParams): string {
  const value = params.value
  if (value === null || value === undefined) return ""
  if (typeof value !== "number") return String(value)
  if (!Number.isFinite(value)) return String(value)
  if (Number.isInteger(value)) return String(value)
  return parseFloat(value.toFixed(4)).toString()
}

/** Default column width in pixels for a raw SQL type. */
export function getColumnWidthPx(rawType?: string): number {
  const normalizedType = normalizeSqlType(rawType)
  if (JSON_TYPES.has(normalizedType)) return 480
  if (TEXT_TYPES.has(normalizedType)) return 384
  if (DATE_TYPES.has(normalizedType)) return 288
  if (BOOLEAN_TYPES.has(normalizedType)) return 160
  if (NUMERIC_TYPES.has(normalizedType)) return 224
  return 288
}

/** Cell renderer that displays a value without any editing affordances. */
export function ReadOnlyCellRenderer({
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

/**
 * Base column def shared by the editable and read-only grids. The grid's own
 * sorting is off everywhere: every grid shows one cursor page, so a client-side
 * sort could only reorder that page. Sorting is done by the API instead, driven
 * from the header components.
 */
export function buildBaseColumnDef(
  column: TableColumnRead,
  savedWidths: Record<string, number>
): ColDef {
  const isNumeric = NUMERIC_TYPES.has(normalizeSqlType(column.type))
  return {
    field: column.name,
    headerName: column.name,
    sortable: false,
    resizable: true,
    width: savedWidths[column.name] ?? getColumnWidthPx(column.type),
    minWidth: 100,
    ...(isNumeric && { valueFormatter: numericValueFormatter }),
  }
}

/** Options for {@link buildEditableColumnDef}. */
export interface EditableColumnDefOptions {
  /**
   * Whether a row's cells may be edited; omitted means every row may. Rows
   * that may not keep the renderer's read-only text and JSON views.
   */
  canEditRow?: (row: unknown) => boolean
  /**
   * For grids that size columns to their content: keep the renderer's hover
   * buttons in the layout so they are part of what gets measured.
   */
  reserveButtonSpace?: boolean
}

/** Narrowest a content-sized column may get. */
export const CONTENT_SIZED_MIN_WIDTH_PX = 80
/**
 * Widest a content-sized column may get on its content alone. Long text and
 * JSON stop here and are read in the cell panel instead.
 */
export const CONTENT_SIZED_MAX_WIDTH_PX = 320
/**
 * Rewrites a column def for a grid that sizes columns to their content. A
 * width the user dragged stays authoritative; every other column drops its
 * per-type default, so re-rendering the defs never undoes a measured width.
 * Unmeasured columns start at the floor, which is also where the grid's fit
 * pass resets them before growing each to its measured width.
 */
export function toContentSizedColumnDef<T extends ColDef>(
  def: T,
  savedWidth: number | undefined
): T {
  return {
    ...def,
    width: savedWidth,
    initialWidth: savedWidth ?? CONTENT_SIZED_MIN_WIDTH_PX,
    minWidth: CONTENT_SIZED_MIN_WIDTH_PX,
  }
}

/**
 * Column def for a grid hosted under a `TablePanelProvider`: inline editors on
 * scalar columns, and the renderer's panel buttons for long text and JSON.
 */
export function buildEditableColumnDef(
  column: TableColumnRead,
  savedWidths: Record<string, number>,
  { canEditRow, reserveButtonSpace }: EditableColumnDefOptions = {}
): ColDef {
  return {
    ...buildBaseColumnDef(column, savedWidths),
    cellRenderer: AgGridCellRenderer,
    cellRendererParams: {
      tableColumn: column,
      canEditRow,
      reserveButtonSpace,
    },
    // JSON columns are edited only via the side panel
    ...(isJsonColumn(column)
      ? { editable: false }
      : {
          cellEditor: AgGridCellEditor,
          cellEditorParams: { tableColumn: column },
          suppressKeyboardEvent: suppressEditorKeys,
          editable: canEditRow ? (params) => canEditRow(params.data) : true,
        }),
  }
}

/**
 * Column defs for a display-only grid: CellDisplay renderers and no editors.
 * Rows keep the API's order.
 */
export function buildReadOnlyColumnDefs(
  columns: readonly TableColumnRead[],
  savedWidths: Record<string, number>
): ColDef[] {
  return columns.map((column): ColDef => {
    return {
      ...buildBaseColumnDef(column, savedWidths),
      cellRenderer: ReadOnlyCellRenderer,
      cellRendererParams: {
        tableColumn: column,
      },
      editable: false,
    }
  })
}
