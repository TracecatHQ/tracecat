"use client"

import { format } from "date-fns"
import type { TableColumnRead } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const TIMESTAMP_TYPES = new Set(["TIMESTAMP", "TIMESTAMPTZ"])
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

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return String(value)
  if (Number.isInteger(value)) return String(value)
  return parseFloat(value.toFixed(2)).toString()
}

function ScientificNumber({ value }: { value: number }) {
  const needsScientific =
    value !== 0 && (Math.abs(value) >= 1e6 || Math.abs(value) < 1e-3)

  if (!needsScientific) {
    return <span>{formatNumber(value)}</span>
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-default">{value.toExponential(3)}</span>
      </TooltipTrigger>
      <TooltipContent>{String(value)}</TooltipContent>
    </Tooltip>
  )
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
  const isDateLike =
    normalizedType === "DATE" || TIMESTAMP_TYPES.has(normalizedType)
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

  let parsedDate: Date | undefined
  if (isDateLike && typeof value === "string" && value) {
    if (normalizedType === "DATE") {
      const [year, month, day] = value.split("-").map(Number)
      parsedDate = new Date(year, month - 1, day)
    } else {
      parsedDate = new Date(value)
    }
  }

  const isValidDate =
    parsedDate && !Number.isNaN(parsedDate.getTime()) ? parsedDate : null
  const selectDisplayValue =
    typeof value === "string"
      ? value
      : value === null || value === undefined
        ? ""
        : String(value)

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
      ) : isValidDate ? (
        <span>
          {format(
            isValidDate,
            normalizedType === "DATE" ? "MMM d yyyy" : "MMM d yyyy '\u00b7' p"
          )}
        </span>
      ) : isNumericColumn && typeof value === "number" ? (
        <ScientificNumber value={value} />
      ) : isNumericColumn &&
        typeof value === "string" &&
        value !== "" &&
        !Number.isNaN(Number(value)) ? (
        <ScientificNumber value={Number(value)} />
      ) : typeof value === "number" ? (
        <ScientificNumber value={value} />
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
