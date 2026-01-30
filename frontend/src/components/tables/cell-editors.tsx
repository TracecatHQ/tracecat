"use client"

import { closeBrackets } from "@codemirror/autocomplete"
import { history } from "@codemirror/commands"
import { json } from "@codemirror/lang-json"
import { bracketMatching } from "@codemirror/language"
import { type Diagnostic, linter, lintGutter } from "@codemirror/lint"
import { EditorView, keymap } from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
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
import { DateTimePicker } from "@/components/ui/date-time-picker"
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
}: CellEditorProps) {
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
      className="h-8 text-xs border-0 rounded-none shadow-none focus-visible:ring-0"
    />
  )
}

function IntegerCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const [localStr, setLocalStr] = useState(() =>
    value === null || value === undefined ? "" : String(value)
  )
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])

  const commitValue = useCallback(() => {
    if (localStr === "" || localStr === "-") {
      onChange(localStr === "" ? null : value)
    } else {
      const parsed = Number.parseInt(localStr, 10)
      onChange(Number.isNaN(parsed) ? null : parsed)
    }
    onCommit()
  }, [localStr, onChange, onCommit, value])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault()
        commitValue()
      } else if (e.key === "Escape") {
        e.preventDefault()
        onCancel()
      } else if (e.key === "Tab") {
        e.preventDefault()
        commitValue()
      }
    },
    [commitValue, onCancel]
  )

  return (
    <Input
      ref={ref}
      type="number"
      step="1"
      value={localStr}
      onChange={(e) => setLocalStr(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={commitValue}
      className="h-8 text-xs border-0 rounded-none shadow-none focus-visible:ring-0"
    />
  )
}

function NumericCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const [localStr, setLocalStr] = useState(() =>
    value === null || value === undefined ? "" : String(value)
  )
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])

  const commitValue = useCallback(() => {
    if (localStr === "" || localStr === "-" || localStr === ".") {
      onChange(localStr === "" ? null : value)
    } else {
      const parsed = Number.parseFloat(localStr)
      onChange(Number.isNaN(parsed) ? null : parsed)
    }
    onCommit()
  }, [localStr, onChange, onCommit, value])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault()
        commitValue()
      } else if (e.key === "Escape") {
        e.preventDefault()
        onCancel()
      } else if (e.key === "Tab") {
        e.preventDefault()
        commitValue()
      }
    },
    [commitValue, onCancel]
  )

  return (
    <Input
      ref={ref}
      type="number"
      step="any"
      value={localStr}
      onChange={(e) => setLocalStr(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={commitValue}
      className="h-8 text-xs border-0 rounded-none shadow-none focus-visible:ring-0"
    />
  )
}

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

function DateCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const stringValue =
    typeof value === "string" && value.length > 0 ? value : undefined
  const parsedDate = stringValue !== undefined ? new Date(stringValue) : null
  const dateValue =
    parsedDate && !Number.isNaN(parsedDate.getTime()) ? parsedDate : null

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault()
        onCancel()
      }
    },
    [onCancel]
  )

  return (
    <div onKeyDown={handleKeyDown}>
      <DateTimePicker
        value={dateValue}
        displayFormat="PPP"
        placeholder="Select date"
        hideTime
        onChange={(next) => {
          if (next) {
            const yyyy = next.getFullYear()
            const mm = String(next.getMonth() + 1).padStart(2, "0")
            const dd = String(next.getDate()).padStart(2, "0")
            onChange(`${yyyy}-${mm}-${dd}`)
          } else {
            onChange("")
          }
          if (next) {
            setTimeout(onCommit, 0)
          }
        }}
        onBlur={onCommit}
        buttonProps={{
          variant: "ghost",
          className:
            "w-full h-8 text-xs rounded-none shadow-none focus-visible:ring-0",
        }}
      />
    </div>
  )
}

function TimestampCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const stringValue =
    typeof value === "string" && value.length > 0 ? value : undefined
  const parsedDate = stringValue !== undefined ? new Date(stringValue) : null
  const dateValue =
    parsedDate && !Number.isNaN(parsedDate.getTime()) ? parsedDate : null

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault()
        onCancel()
      }
    },
    [onCancel]
  )

  return (
    <div onKeyDown={handleKeyDown}>
      <DateTimePicker
        value={dateValue}
        onChange={(next) => {
          onChange(next ? next.toISOString() : "")
          if (next) {
            setTimeout(onCommit, 0)
          }
        }}
        onBlur={onCommit}
        buttonProps={{
          variant: "ghost",
          className:
            "w-full h-8 text-xs rounded-none shadow-none focus-visible:ring-0",
        }}
      />
    </div>
  )
}

function jsonLinter(view: EditorView): Diagnostic[] {
  const content = view.state.doc.toString()
  if (!content.trim()) return []
  try {
    JSON.parse(content)
    return []
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Invalid JSON"
    const posMatch = msg.match(/position (\d+)/)
    const pos = posMatch ? Number.parseInt(posMatch[1], 10) : 0
    const from = Math.min(pos, content.length)
    const to = Math.min(from + 1, content.length)
    return [{ from, to, severity: "error", message: msg, source: "json" }]
  }
}

function JsonCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const strValue =
    typeof value === "string"
      ? value
      : value === null || value === undefined
        ? ""
        : JSON.stringify(value, null, 2)
  const [localValue, setLocalValue] = useState(strValue)
  const [error, setError] = useState<string | null>(null)

  const validate = useCallback((val: string): boolean => {
    if (val.trim() === "") return true
    try {
      JSON.parse(val)
      return true
    } catch {
      return false
    }
  }, [])

  const handleCommitRef = useRef<() => void>(() => {})
  const onCancelRef = useRef(onCancel)
  onCancelRef.current = onCancel

  const handleCommit = useCallback(() => {
    if (!validate(localValue)) {
      setError("Invalid JSON")
      return
    }
    setError(null)
    if (localValue.trim() === "") {
      onChange(null)
    } else {
      onChange(JSON.parse(localValue))
    }
    onCommit()
  }, [localValue, validate, onChange, onCommit])

  handleCommitRef.current = handleCommit

  const extensions = useMemo(
    () => [
      json(),
      lintGutter(),
      linter(jsonLinter),
      history(),
      bracketMatching(),
      closeBrackets(),
      keymap.of([
        {
          key: "Mod-Enter",
          run: () => {
            handleCommitRef.current()
            return true
          },
          preventDefault: true,
        },
        {
          key: "Escape",
          run: () => {
            onCancelRef.current()
            return true
          },
          preventDefault: true,
        },
      ]),
      EditorView.theme({
        ".cm-content": { fontFamily: "monospace", fontSize: "12px" },
        ".cm-scroller": { maxHeight: "300px", overflow: "auto" },
      }),
    ],
    []
  )

  return (
    <div className="space-y-1">
      <CodeMirror
        value={localValue}
        onChange={(val) => {
          setLocalValue(val)
          if (error) {
            setError(validate(val) ? null : "Invalid JSON")
          }
        }}
        height="auto"
        extensions={extensions}
        theme="light"
        autoFocus
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
          highlightActiveLine: true,
          bracketMatching: false,
          closeBrackets: false,
          history: false,
          defaultKeymap: true,
          syntaxHighlighting: true,
          autocompletion: false,
        }}
        className="min-h-[100px] max-h-[300px] overflow-auto rounded-md border font-mono text-xs"
        onBlur={handleCommit}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
      <p className="text-xs text-muted-foreground">Cmd/Ctrl+Enter to save</p>
    </div>
  )
}

function UuidCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const strValue = value === null || value === undefined ? "" : String(value)
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])

  const uuidRegex =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

  const handleCommit = useCallback(() => {
    const val = typeof value === "string" ? value : String(value ?? "")
    if (val.trim() !== "" && !uuidRegex.test(val.trim())) {
      setError("Invalid UUID format")
      return
    }
    setError(null)
    onCommit()
  }, [value, onCommit])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault()
        handleCommit()
      } else if (e.key === "Escape") {
        e.preventDefault()
        onCancel()
      } else if (e.key === "Tab") {
        e.preventDefault()
        handleCommit()
      }
    },
    [handleCommit, onCancel]
  )

  return (
    <div className="space-y-1">
      <Input
        ref={ref}
        type="text"
        value={strValue}
        onChange={(e) => {
          onChange(e.target.value)
          if (error && uuidRegex.test(e.target.value.trim())) {
            setError(null)
          }
        }}
        onKeyDown={handleKeyDown}
        onBlur={handleCommit}
        placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        className="h-8 font-mono text-xs border-0 rounded-none shadow-none focus-visible:ring-0"
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
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
        sideOffset={6}
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
      return <IntegerCellEditor {...props} />
    case "NUMERIC":
    case "DECIMAL":
    case "REAL":
    case "FLOAT":
    case "FLOAT4":
    case "FLOAT8":
    case "DOUBLE":
    case "DOUBLE PRECISION":
      return <NumericCellEditor {...props} />
    case "BOOLEAN":
    case "BOOL":
      return <BooleanCellEditor {...props} />
    case "DATE":
      return <DateCellEditor {...props} />
    case "TIMESTAMP":
    case "TIMESTAMPTZ":
      return <TimestampCellEditor {...props} />
    case "JSON":
    case "JSONB":
      return <JsonCellEditor {...props} />
    case "UUID":
      return <UuidCellEditor {...props} />
    case "SELECT":
      return <SelectCellEditor {...props} />
    case "MULTI_SELECT":
      return <MultiSelectCellEditor {...props} />
    default:
      return <TextCellEditor {...props} />
  }
}
