"use client"

import type { TableColumnRead } from "@/client"
import { Badge } from "@/components/ui/badge"

const INTEGER_TYPES = new Set([
  "INT",
  "INTEGER",
  "BIGINT",
  "SMALLINT",
  "BIGSERIAL",
  "SERIAL",
  "SERIAL4",
  "SERIAL8",
])

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

const normalizeSqlType = (rawType?: string) => {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

const sanitizeColumnOptions = (options?: Array<string> | null) => {
  if (!Array.isArray(options)) {
    return undefined
  }
  const normalized = options
    .map((option) => (typeof option === "string" ? option.trim() : ""))
    .filter((option): option is string => option.length > 0)
  return normalized.length > 0 ? normalized : undefined
}

/** Format a number as plain text (no scientific notation). */
function formatNumberDisplay(value: number, isInteger: boolean): string {
  if (!Number.isFinite(value)) return String(value)
  if (isInteger) {
    // Use toFixed(0) to avoid JS scientific notation for large integers
    return value.toFixed(0)
  }
  if (Number.isInteger(value)) return value.toFixed(0)
  return parseFloat(value.toFixed(4)).toString()
}

/**
 * Format a date string as ISO YYYY-MM-DD.
 * Returns the raw string if it's already in that format to avoid timezone shifts.
 */
function formatDateISO(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, "0")
  const dd = String(d.getDate()).padStart(2, "0")
  return `${yyyy}-${mm}-${dd}`
}

/** Format a timestamp string as ISO. */
function formatTimestampISO(value: string): string {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toISOString()
}

export function CellDisplay({
  value,
  column,
}: {
  value: unknown
  column: TableColumnRead
}) {
  const normalizedType = normalizeSqlType(column.type)
  const isNumericColumn = NUMERIC_TYPES.has(normalizedType)
  const isIntegerColumn = INTEGER_TYPES.has(normalizedType)
  const isDate = normalizedType === "DATE"
  const isTimestamp = normalizedType === "TIMESTAMPTZ"
  const isSelectColumn = normalizedType === "SELECT"
  const isMultiSelectColumn = normalizedType === "MULTI_SELECT"
  const columnOptions = sanitizeColumnOptions(column.options)
  const parsedMultiValue = Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : typeof value === "string" && value.length > 0
      ? [value]
      : []
  const multiSelectValues =
    columnOptions && columnOptions.length > 0
      ? parsedMultiValue.filter((item) => columnOptions.includes(item))
      : parsedMultiValue

  const selectDisplayValue =
    typeof value === "string"
      ? value
      : value === null || value === undefined
        ? ""
        : String(value)

  const isEmpty = value === null || value === undefined || value === ""

  return (
    <div className="flex items-center h-full w-full text-xs font-sans text-foreground">
      {isMultiSelectColumn ? (
        multiSelectValues.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {multiSelectValues.map((item, idx) => (
              <Badge
                key={`${column.id}-${item}-${idx}`}
                variant="secondary"
                className="text-[11px]"
              >
                {item}
              </Badge>
            ))}
          </div>
        ) : (
          <span className="text-muted-foreground">&mdash;</span>
        )
      ) : isSelectColumn ? (
        <span className="truncate">{selectDisplayValue || "\u2014"}</span>
      ) : typeof value === "object" && value ? (
        <span className="truncate text-xs font-sans">
          {JSON.stringify(value)}
        </span>
      ) : isDate ? (
        isEmpty ? (
          <span className="text-muted-foreground">YYYY-MM-DD</span>
        ) : (
          <span>{formatDateISO(String(value))}</span>
        )
      ) : isTimestamp ? (
        isEmpty ? (
          <span className="text-muted-foreground">YYYY-MM-DDTHH:mm:ss.Z</span>
        ) : (
          <span>{formatTimestampISO(String(value))}</span>
        )
      ) : isNumericColumn && typeof value === "number" ? (
        <span>{formatNumberDisplay(value, isIntegerColumn)}</span>
      ) : isNumericColumn && typeof value === "string" && value !== "" ? (
        <span>{value}</span>
      ) : typeof value === "number" ? (
        <span>{formatNumberDisplay(value, false)}</span>
      ) : typeof value === "string" && value.length > 25 ? (
        <span className="truncate text-xs font-sans">{String(value)}</span>
      ) : (
        <span className="truncate">
          {value === null || value === undefined ? "" : String(value)}
        </span>
      )}
    </div>
  )
}
