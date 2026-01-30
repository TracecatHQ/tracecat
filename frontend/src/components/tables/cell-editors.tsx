"use client"

import { history } from "@codemirror/commands"
import { json } from "@codemirror/lang-json"
import { bracketMatching, closeBrackets } from "@codemirror/language"
import { type Diagnostic, linter, lintGutter } from "@codemirror/lint"
import { EditorView, keymap } from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import type React from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
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
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"

export type CellEditorProps = {
  value: unknown
  column: TableColumnRead
  onChange: (value: unknown) => void
  onCommit: () => void
  onCancel: () => void
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
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    ref.current?.focus()
  }, [])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
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
    <Textarea
      ref={ref}
      value={strValue}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={onCommit}
      className="min-h-[72px] w-full text-xs border rounded bg-background shadow-sm focus-visible:ring-1"
      rows={3}
    />
  )
}

function IntegerCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const numValue =
    typeof value === "number"
      ? value
      : value === null || value === undefined
        ? ""
        : Number(value)
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
      type="number"
      step="1"
      value={numValue}
      onChange={(e) => {
        const val = e.target.value
        onChange(val === "" ? null : Number.parseInt(val, 10))
      }}
      onKeyDown={handleKeyDown}
      onBlur={onCommit}
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
  const numValue =
    typeof value === "number"
      ? value
      : value === null || value === undefined
        ? ""
        : Number(value)
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
      type="number"
      step="any"
      value={numValue}
      onChange={(e) => {
        const val = e.target.value
        onChange(val === "" ? null : Number.parseFloat(val))
      }}
      onKeyDown={handleKeyDown}
      onBlur={onCommit}
      className="h-8 text-xs border-0 rounded-none shadow-none focus-visible:ring-0"
    />
  )
}

function BooleanCellEditor({ value, onChange, onCommit }: CellEditorProps) {
  const boolValue = value === true || value === "true" || value === "1"

  const handleToggle = useCallback(
    (checked: boolean) => {
      onChange(checked)
      // Commit immediately on toggle
      setTimeout(onCommit, 0)
    },
    [onChange, onCommit]
  )

  return (
    <div className="flex items-center gap-2 p-1">
      <Switch checked={boolValue} onCheckedChange={handleToggle} />
      <span className="text-xs text-muted-foreground">
        {boolValue ? "true" : "false"}
      </span>
    </div>
  )
}

function DateCellEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const strValue = typeof value === "string" ? value : ""
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    ref.current?.focus()
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
      type="date"
      value={strValue}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={onCommit}
      className="h-8 text-xs border-0 rounded-none shadow-none focus-visible:ring-0"
    />
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
        buttonProps={{ className: "w-full h-8 text-xs" }}
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
        <SelectTrigger className="h-8 text-xs" autoFocus>
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
}: CellEditorProps) {
  const options = getColumnOptions(column)
  const suggestions =
    options?.map((option) => ({
      id: option,
      label: option,
      value: option,
    })) ?? []
  const currentValue = Array.isArray(value)
    ? (value as string[])
    : typeof value === "string" && value
      ? [value]
      : []

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
    <div onKeyDown={handleKeyDown} onBlur={onCommit}>
      <MultiTagCommandInput
        value={currentValue}
        onChange={(values) => onChange(values)}
        suggestions={suggestions}
        placeholder="Select values..."
        allowCustomTags={!options || options.length === 0}
        searchKeys={["label", "value"]}
        className="w-full text-xs"
      />
    </div>
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
