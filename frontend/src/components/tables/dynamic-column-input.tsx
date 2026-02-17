"use client"

import type { ControllerRenderProps } from "react-hook-form"
import type { TableColumnRead } from "@/client"
import { MultiTagCommandInput } from "@/components/tags-input"
import { DateTimePicker } from "@/components/ui/date-time-picker"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export function getColumnOptions(column: TableColumnRead) {
  const rawOptions = column.options
  if (!Array.isArray(rawOptions)) {
    return undefined
  }
  const normalized = rawOptions
    .map((option) => (typeof option === "string" ? option.trim() : ""))
    .filter((option): option is string => option.length > 0)
  return normalized.length > 0 ? normalized : undefined
}

export function DynamicInput({
  column,
  field,
}: {
  column: TableColumnRead
  field: ControllerRenderProps<Record<string, unknown>, string>
}) {
  const normalizedType = column.type.toUpperCase()
  const options = getColumnOptions(column)

  switch (normalizedType) {
    case "BOOLEAN":
      return (
        <Input
          type="text"
          placeholder="true, false, 1, or 0"
          value={field.value as string}
          onChange={(e) => field.onChange(e.target.value)}
        />
      )
    case "INTEGER":
      return (
        <Input
          type="number"
          placeholder="Enter an integer"
          value={
            field.value === null || field.value === undefined
              ? ""
              : (field.value as number)
          }
          onChange={(e) =>
            field.onChange(
              e.target.value === "" ? null : Number(e.target.value)
            )
          }
        />
      )
    case "NUMERIC":
      return (
        <Input
          type="number"
          step="any"
          placeholder="Enter a number"
          value={
            field.value === null || field.value === undefined
              ? ""
              : (field.value as number)
          }
          onChange={(e) =>
            field.onChange(
              e.target.value === "" ? null : Number(e.target.value)
            )
          }
        />
      )
    case "JSONB":
      return (
        <Input
          type="text"
          placeholder='{"key": "value"}'
          value={field.value as string}
          onChange={(e) => field.onChange(e.target.value)}
        />
      )
    case "TIMESTAMPTZ": {
      const stringValue =
        typeof field.value === "string" && field.value.length > 0
          ? field.value
          : undefined
      const parsedDate =
        stringValue !== undefined ? new Date(stringValue) : null
      const dateValue =
        parsedDate && !Number.isNaN(parsedDate.getTime()) ? parsedDate : null

      return (
        <DateTimePicker
          value={dateValue}
          onChange={(next) => field.onChange(next ? next.toISOString() : "")}
          onBlur={field.onBlur}
          buttonProps={{ className: "w-full" }}
        />
      )
    }
    case "SELECT": {
      if (options && options.length > 0) {
        const stringValue =
          typeof field.value === "string" ? field.value : undefined
        return (
          <Select
            value={stringValue}
            onValueChange={(value) => field.onChange(value)}
          >
            <SelectTrigger>
              <SelectValue placeholder="Choose a value" />
            </SelectTrigger>
            <SelectContent>
              {options.map((option) => (
                <SelectItem key={option} value={option}>
                  {option}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )
      }
      return (
        <Input
          type="text"
          placeholder="Enter a value"
          value={field.value as string}
          onChange={(e) => field.onChange(e.target.value)}
        />
      )
    }
    case "MULTI_SELECT": {
      const suggestions =
        options?.map((option) => ({
          id: option,
          label: option,
          value: option,
        })) ?? []
      const currentValue = Array.isArray(field.value)
        ? (field.value as string[])
        : typeof field.value === "string" && field.value
          ? [field.value]
          : []

      return (
        <MultiTagCommandInput
          value={currentValue}
          onChange={(values) => field.onChange(values)}
          suggestions={suggestions}
          placeholder="Select values..."
          allowCustomTags={!options || options.length === 0}
          searchKeys={["label", "value"]}
          className="w-full"
        />
      )
    }
    case "DATE": {
      const stringValue =
        typeof field.value === "string" && field.value.length > 0
          ? field.value
          : ""
      return (
        <Input
          type="date"
          value={stringValue}
          onChange={(e) => field.onChange(e.target.value)}
        />
      )
    }
    case "TEXT":
    default:
      return (
        <Input
          type="text"
          placeholder="Enter text"
          value={field.value as string}
          onChange={(e) => field.onChange(e.target.value)}
        />
      )
  }
}
