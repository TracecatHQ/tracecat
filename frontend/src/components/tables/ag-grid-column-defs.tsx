"use client"

import type {
  ColDef,
  SuppressKeyboardEventParams,
  ValueFormatterParams,
} from "ag-grid-community"
import type { TableColumnRead } from "@/client"
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

/** Base column def shared by the editable and read-only grids. */
export function buildBaseColumnDef(
  column: TableColumnRead,
  savedWidths: Record<string, number>
): ColDef {
  const isNumeric = NUMERIC_TYPES.has(normalizeSqlType(column.type))
  return {
    field: column.name,
    headerName: column.name,
    sortable: true,
    resizable: true,
    width: savedWidths[column.name] ?? getColumnWidthPx(column.type),
    minWidth: 100,
    ...(isNumeric && { valueFormatter: numericValueFormatter }),
  }
}

/**
 * Column defs for a display-only grid: CellDisplay renderers, no editors, and
 * no header sorting. The read-only grid is always fed one cursor page, so a
 * client-side sort could only reorder the visible page and would silently
 * revert on the next one; rows keep the API's order instead.
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
      sortable: false,
    }
  })
}
