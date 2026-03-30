"use client"

import { Check, ChevronsUpDown } from "lucide-react"
import type React from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { TableColumnRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

export type CellEditorProps = {
  value: unknown
  column: TableColumnRead
  onChange: (value: unknown) => void
  onCommit: () => void
  onCancel: () => void
  /** Actual pixel width of the AG Grid cell, used to size dropdowns */
  cellWidth?: number
}

const normalizeSqlType = (rawType?: string) => {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

const getColumnOptions = (column: TableColumnRead) => {
  const rawOptions = column.options
  if (!Array.isArray(rawOptions)) {
    return undefined
  }
  const normalized = rawOptions
    .map((option) => (typeof option === "string" ? option.trim() : ""))
    .filter((option): option is string => option.length > 0)
  return normalized.length > 0 ? normalized : undefined
}

function TextCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
  placeholder,
}: CellEditorProps & { placeholder?: string }) {
  const strValue = value === null || value === undefined ? "" : String(value)
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault()
        onCommit()
      } else if (e.key === "Escape") {
        e.preventDefault()
        onCancel()
      } else if (e.key === "Tab") {
        e.preventDefault()
        onCommit()
      }
    },
    [onCommit, onCancel]
  )

  return (
    <Input
      ref={ref}
      type="text"
      value={strValue}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={onCommit}
      placeholder={placeholder}
      className="h-8 text-xs border-0 rounded-none shadow-none focus-visible:ring-0"
    />
  )
}

/**
 * Text editor that validates on commit. If parsing fails, silently reverts
 * to the original value instead of showing an error.
 */
function ValidatedTextEditor({
  value,
  onChange,
  onCommit,
  onCancel,
  column,
  cellWidth,
  parse,
  format,
  placeholder,
}: CellEditorProps & {
  parse: (raw: string) => unknown
  format: (v: unknown) => string
  placeholder?: string
}) {
  const originalValue = useRef(value)
  // Local string state — not reformatted on every keystroke so the user
  // can type intermediate values like "1." without losing the dot.
  const [localStr, setLocalStr] = useState(() => format(value))

  const handleChange = useCallback((v: unknown) => {
    setLocalStr(String(v ?? ""))
  }, [])

  const handleCommit = useCallback(() => {
    const raw = localStr.trim()
    if (raw === "") {
      onChange(null)
      onCommit()
      return
    }
    try {
      const parsed = parse(raw)
      onChange(parsed)
    } catch {
      // Revert to original value on invalid input
      onChange(originalValue.current)
    }
    onCommit()
  }, [localStr, parse, onChange, onCommit])

  return (
    <TextCellEditor
      value={localStr}
      column={column}
      onChange={handleChange}
      onCommit={handleCommit}
      onCancel={onCancel}
      cellWidth={cellWidth}
      placeholder={placeholder}
    />
  )
}

// --- Parsers for ValidatedTextEditor ---

function parseInteger(raw: string): number {
  if (!/^-?\d+$/.test(raw)) throw new Error("Invalid integer")
  return Number.parseInt(raw, 10)
}

function parseNumeric(raw: string): string {
  const n = Number(raw)
  if (!Number.isFinite(n)) throw new Error("Invalid number")
  // Return as string to preserve precision (backend handles conversion)
  return raw.trim()
}

function parseDate(raw: string): string {
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) throw new Error("Invalid date")
  const yyyy = d.getUTCFullYear()
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0")
  const dd = String(d.getUTCDate()).padStart(2, "0")
  return `${yyyy}-${mm}-${dd}`
}

function parseTimestamp(raw: string): string {
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) throw new Error("Invalid timestamp")
  return d.toISOString()
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return ""
  return String(v)
}

function formatNumeric(v: unknown): string {
  if (v === null || v === undefined) return ""
  const num = typeof v === "number" ? v : Number.parseFloat(String(v))
  if (Number.isNaN(num)) return String(v)
  if (!Number.isInteger(num)) {
    return Number.parseFloat(num.toFixed(4)).toString()
  }
  return String(num)
}

// --- Editors ---

function BooleanCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const boolValue = value === true || value === "true" || value === "1"
  const strValue = boolValue ? "true" : "false"

  return (
    <Select
      value={strValue}
      onValueChange={(val) => {
        onChange(val === "true")
        setTimeout(onCommit, 0)
      }}
    >
      <SelectTrigger
        className="h-8 text-xs border-0 rounded-none shadow-none ring-0 focus:ring-0 focus:outline-none bg-transparent"
        autoFocus
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent
        onEscapeKeyDown={(e) => {
          e.preventDefault()
          onCancel()
        }}
      >
        <SelectItem value="true">True</SelectItem>
        <SelectItem value="false">False</SelectItem>
      </SelectContent>
    </Select>
  )
}

function SelectCellEditor({
  value,
  column,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const options = getColumnOptions(column)
  const strValue = typeof value === "string" ? value : ""

  if (options && options.length > 0) {
    return (
      <Select
        value={strValue}
        onValueChange={(val) => {
          onChange(val)
          setTimeout(onCommit, 0)
        }}
      >
        <SelectTrigger
          className="h-8 text-xs border-0 rounded-none shadow-none ring-0 focus:ring-0 focus:outline-none bg-transparent"
          autoFocus
        >
          <SelectValue placeholder="Choose a value" />
        </SelectTrigger>
        <SelectContent
          position="popper"
          side="bottom"
          avoidCollisions
          collisionPadding={8}
          onEscapeKeyDown={(e) => {
            e.preventDefault()
            onCancel()
          }}
        >
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
    <TextCellEditor
      value={value}
      column={column}
      onChange={onChange}
      onCommit={onCommit}
      onCancel={onCancel}
    />
  )
}

function MultiSelectCellEditor({
  value,
  column,
  onChange,
  onCommit,
  onCancel,
  cellWidth,
}: CellEditorProps) {
  const options = getColumnOptions(column)
  const [open, setOpen] = useState(true)

  const currentValue = useMemo(
    () =>
      Array.isArray(value)
        ? (value as string[])
        : typeof value === "string" && value
          ? [value]
          : [],
    [value]
  )

  const valueSet = useMemo(() => new Set(currentValue), [currentValue])

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      setOpen(nextOpen)
      if (!nextOpen) {
        onCommit()
      }
    },
    [onCommit]
  )

  if (!options || options.length === 0) {
    return (
      <TextCellEditor
        value={value}
        column={column}
        onChange={onChange}
        onCommit={onCommit}
        onCancel={onCancel}
      />
    )
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          role="combobox"
          className="flex h-8 w-full items-center justify-between bg-transparent px-3 text-xs outline-none"
        >
          <span className="truncate text-left">
            {currentValue.length === 0
              ? "Select values..."
              : currentValue.length === 1
                ? currentValue[0]
                : `${currentValue.length} selected`}
          </span>
          <ChevronsUpDown className="ml-2 size-3 opacity-50" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="!w-auto p-0"
        align="start"
        side="bottom"
        sideOffset={6}
        avoidCollisions
        collisionPadding={8}
        style={{
          width: cellWidth
            ? `${cellWidth - 28}px`
            : "var(--radix-popover-trigger-width)",
        }}
        onEscapeKeyDown={(e) => {
          e.preventDefault()
          onCancel()
        }}
      >
        <Command>
          <CommandInput placeholder="Search options..." className="text-xs" />
          <CommandList>
            <CommandEmpty>No options found.</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const isSelected = valueSet.has(option)
                return (
                  <CommandItem
                    key={option}
                    value={option}
                    className="relative flex w-full cursor-default select-none items-center rounded-sm py-1.5 pl-2 pr-8 text-xs outline-none [&_svg]:size-3.5"
                    onSelect={() => {
                      const nextValue = isSelected
                        ? currentValue.filter((item) => item !== option)
                        : [...currentValue, option]
                      onChange(nextValue)
                      setOpen(true)
                    }}
                  >
                    <span className="absolute right-2 flex size-3.5 items-center justify-center">
                      <Check
                        className={cn("size-4", !isSelected && "opacity-0")}
                        aria-hidden
                      />
                    </span>
                    <span className="truncate">{option}</span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
        {currentValue.length > 0 && (
          <div className="border-t p-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-full text-xs"
              onClick={() => onChange([])}
            >
              Clear selection
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

export function CellEditor(props: CellEditorProps) {
  const normalizedType = normalizeSqlType(props.column.type)

  switch (normalizedType) {
    case "TEXT":
      return <TextCellEditor {...props} />
    case "INTEGER":
    case "INT":
    case "BIGINT":
    case "SMALLINT":
    case "SERIAL":
      return (
        <ValidatedTextEditor
          {...props}
          parse={parseInteger}
          format={formatValue}
        />
      )
    case "NUMERIC":
    case "DECIMAL":
    case "REAL":
    case "FLOAT":
    case "FLOAT4":
    case "FLOAT8":
    case "DOUBLE":
    case "DOUBLE PRECISION":
      return (
        <ValidatedTextEditor
          {...props}
          parse={parseNumeric}
          format={formatNumeric}
        />
      )
    case "BOOLEAN":
    case "BOOL":
      return <BooleanCellEditor {...props} />
    case "DATE":
      return (
        <ValidatedTextEditor
          {...props}
          parse={parseDate}
          format={formatValue}
          placeholder="YYYY-MM-DD"
        />
      )
    case "TIMESTAMPTZ":
      return (
        <ValidatedTextEditor
          {...props}
          parse={parseTimestamp}
          format={formatValue}
          placeholder="YYYY-MM-DDTHH:mm:ss.Z"
        />
      )
    case "SELECT":
      return <SelectCellEditor {...props} />
    case "MULTI_SELECT":
      return <MultiSelectCellEditor {...props} />
    default:
      return <TextCellEditor {...props} />
  }
}
