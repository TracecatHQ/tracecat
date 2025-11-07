"use client"

import type { ControllerRenderProps } from "react-hook-form"
import { z } from "zod"
import type { TableColumnRead, TableRead } from "@/client"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
import { DateTimePicker } from "@/components/ui/date-time-picker"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import type { SqlType } from "@/lib/data-type"
import { getColumnEnumValues } from "@/lib/tables"

export type DynamicFormData = Record<string, unknown>

const EMPTY_SELECT_VALUE = "__tracecat_empty__"

const trimString = z.string().transform((val) => val.trim())

function stringInputSchema(column: TableColumnRead) {
  if (column.nullable === false) {
    return trimString.pipe(z.string().min(1, `${column.name} is required`))
  }
  return trimString.transform((val) => (val.length === 0 ? null : val))
}

function numberInputSchema(column: TableColumnRead, kind: "int" | "float") {
  const base = trimString
  const validator =
    kind === "int"
      ? base.refine(
          (val) => val.length === 0 || Number.isInteger(Number(val)),
          `${column.name} must be an integer`
        )
      : base.refine(
          (val) => val.length === 0 || !Number.isNaN(Number(val)),
          `${column.name} must be a number`
        )

  const transformed = validator.transform((val) => {
    if (val.length === 0) return null
    const parsed = kind === "int" ? Number.parseInt(val, 10) : Number(val)
    return Number.isNaN(parsed) ? undefined : parsed
  })

  return column.nullable === false
    ? transformed.refine(
        (val) => typeof val === "number",
        `${column.name} is required`
      )
    : transformed.optional()
}

const BOOLEAN_VALUES = new Set(["true", "false", "1", "0"])

function booleanInputSchema(column: TableColumnRead) {
  const base = trimString
  const refined = base.refine(
    (val) => val.length === 0 || BOOLEAN_VALUES.has(val.toLowerCase()),
    `${column.name} must be true, false, 1, or 0`
  )

  const transformed = refined.transform((val) => {
    if (val.length === 0) return null
    const normalised = val.toLowerCase()
    return normalised === "true" || normalised === "1"
  })

  return column.nullable === false
    ? transformed.refine(
        (val) => typeof val === "boolean",
        `${column.name} is required`
      )
    : transformed.optional()
}

function jsonInputSchema(column: TableColumnRead) {
  const base = trimString
  const transformed = base.transform((val) => {
    if (val.length === 0) return null
    try {
      return JSON.parse(val)
    } catch {
      throw new Error(`${column.name} must be valid JSON`)
    }
  })

  if (column.nullable === false) {
    return transformed.refine(
      (val) => val !== undefined && val !== null,
      `${column.name} is required`
    )
  }
  return transformed.optional()
}

function timestampInputSchema(column: TableColumnRead) {
  const base = trimString
  const refined = base.refine(
    (val) => val.length === 0 || !Number.isNaN(new Date(val).getTime()),
    `${column.name} must be a valid date and time`
  )
  const transformed = refined.transform((val) =>
    val.length === 0 ? null : new Date(val).toISOString()
  )

  return column.nullable === false
    ? transformed.refine(
        (val) => typeof val === "string",
        `${column.name} is required`
      )
    : transformed.optional()
}

function enumInputSchema(column: TableColumnRead) {
  const enumValues = getColumnEnumValues(column)
  const base = trimString
  const refined =
    enumValues.length > 0
      ? base.refine(
          (val) => val.length === 0 || enumValues.includes(val),
          `${column.name} must be one of: ${enumValues.join(", ")}`
        )
      : base

  const transformed = refined.transform((val) =>
    val.length === 0 ? null : val
  )

  return column.nullable === false
    ? transformed.refine(
        (val) => typeof val === "string",
        `${column.name} is required`
      )
    : transformed.optional()
}

export function createRowSchema(table: TableRead) {
  const shape: Record<string, z.ZodTypeAny> = {}

  for (const column of table.columns) {
    switch (column.type.toUpperCase()) {
      case "TEXT":
        shape[column.name] = stringInputSchema(column)
        break
      case "INTEGER":
        shape[column.name] = numberInputSchema(column, "int")
        break
      case "NUMERIC":
        shape[column.name] = numberInputSchema(column, "float")
        break
      case "BOOLEAN":
        shape[column.name] = booleanInputSchema(column)
        break
      case "JSONB":
        shape[column.name] = jsonInputSchema(column)
        break
      case "TIMESTAMP":
      case "TIMESTAMPTZ":
        shape[column.name] = timestampInputSchema(column)
        break
      case "ENUM":
        shape[column.name] = enumInputSchema(column)
        break
      default:
        shape[column.name] = stringInputSchema(column)
        break
    }
  }

  return z.object(shape)
}

function serialiseValueForField(
  column: TableColumnRead,
  value: unknown
): string | undefined {
  if (value === undefined) return undefined
  if (value === null) return ""

  switch (column.type.toUpperCase()) {
    case "BOOLEAN":
      if (typeof value === "boolean") return value ? "true" : "false"
      return String(value)
    case "INTEGER":
    case "NUMERIC":
      return typeof value === "number" && Number.isFinite(value)
        ? value.toString()
        : String(value)
    case "JSONB":
      if (typeof value === "string") return value
      try {
        return JSON.stringify(value, null, 2)
      } catch {
        return ""
      }
    case "TIMESTAMP":
    case "TIMESTAMPTZ":
      if (typeof value === "string") return value
      if (value instanceof Date) return value.toISOString()
      return String(value)
    default:
      return String(value)
  }
}

export function buildInitialValues(
  table: TableRead,
  row: Record<string, unknown>
): DynamicFormData {
  const initial: DynamicFormData = {}
  for (const column of table.columns) {
    initial[column.name] = serialiseValueForField(column, row[column.name])
  }
  return initial
}

export function TableRowFieldInput({
  column,
  field,
}: {
  column: TableColumnRead
  field: ControllerRenderProps<DynamicFormData, string>
}) {
  const rawValue = field.value
  const value =
    typeof rawValue === "string"
      ? rawValue
      : rawValue == null
        ? ""
        : String(rawValue)

  switch (column.type.toUpperCase()) {
    case "BOOLEAN": {
      const selectValue =
        rawValue === undefined || rawValue === null || value === ""
          ? EMPTY_SELECT_VALUE
          : value
      return (
        <Select
          value={selectValue}
          onValueChange={(next) =>
            field.onChange(next === EMPTY_SELECT_VALUE ? undefined : next)
          }
        >
          <SelectTrigger>
            <SelectValue placeholder="Select…" />
          </SelectTrigger>
          <SelectContent>
            {column.nullable !== false && (
              <SelectItem value={EMPTY_SELECT_VALUE}>
                <span className="text-muted-foreground">No value</span>
              </SelectItem>
            )}
            <SelectItem value="true">true</SelectItem>
            <SelectItem value="false">false</SelectItem>
            <SelectItem value="1">1</SelectItem>
            <SelectItem value="0">0</SelectItem>
          </SelectContent>
        </Select>
      )
    }
    case "INTEGER":
    case "NUMERIC":
      return (
        <Input
          type="number"
          inputMode="decimal"
          value={value}
          onChange={(event) => field.onChange(event.target.value)}
        />
      )
    case "JSONB":
      return (
        <Textarea
          rows={4}
          placeholder='{"key": "value"}'
          value={value}
          onChange={(event) => field.onChange(event.target.value)}
        />
      )
    case "TIMESTAMP":
    case "TIMESTAMPTZ": {
      const parsed = value ? new Date(value) : null
      const dateValue =
        parsed && !Number.isNaN(parsed.getTime()) ? parsed : null
      return (
        <DateTimePicker
          value={dateValue}
          onChange={(next) => field.onChange(next ? next.toISOString() : "")}
          onBlur={field.onBlur}
          buttonProps={{ className: "w-full" }}
        />
      )
    }
    case "ENUM": {
      const options = getColumnEnumValues(column)
      const selectValue =
        rawValue === undefined || rawValue === null || value === ""
          ? EMPTY_SELECT_VALUE
          : value
      return (
        <Select
          value={selectValue}
          onValueChange={(next) =>
            field.onChange(next === EMPTY_SELECT_VALUE ? undefined : next)
          }
          disabled={options.length === 0 && column.nullable === false}
        >
          <SelectTrigger>
            <SelectValue
              placeholder={
                options.length === 0 ? "No options available" : "Select…"
              }
            />
          </SelectTrigger>
          <SelectContent>
            {column.nullable !== false && (
              <SelectItem value={EMPTY_SELECT_VALUE}>
                <span className="text-muted-foreground">No value</span>
              </SelectItem>
            )}
            {options.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
    }
    default:
      return (
        <Input
          type="text"
          value={value}
          onChange={(event) => field.onChange(event.target.value)}
        />
      )
  }
}

export function TableRowFieldLabel({ column }: { column: TableColumnRead }) {
  return (
    <span className="flex items-center gap-2">
      <span>{column.name}</span>
      <SqlTypeBadge type={column.type as SqlType} />
      {column.nullable === false ? (
        <span className="text-xs text-muted-foreground">(required)</span>
      ) : null}
    </span>
  )
}
