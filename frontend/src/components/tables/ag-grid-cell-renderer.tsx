import { closeBrackets } from "@codemirror/autocomplete"
import { history } from "@codemirror/commands"
import { json } from "@codemirror/lang-json"
import { bracketMatching } from "@codemirror/language"
import { type Diagnostic, linter, lintGutter } from "@codemirror/lint"
import { EditorView, keymap } from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import type { CustomCellRendererProps } from "ag-grid-react"
import { Eye, NotebookPen, Pencil } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { TableColumnRead } from "@/client"
import { JsonViewWithControls } from "@/components/json-viewer"
import { CellDisplay } from "@/components/tables/cell-display"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

interface AgGridCellRendererParams extends CustomCellRendererProps {
  tableColumn: TableColumnRead
}

const JSON_TYPES = new Set(["JSON", "JSONB"])
const SELECT_TYPES = new Set(["SELECT", "MULTI_SELECT"])
const DATE_TYPES = new Set(["DATE", "TIMESTAMP", "TIMESTAMPTZ"])

function normalizeSqlType(rawType?: string) {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

export function AgGridCellRenderer(params: AgGridCellRendererParams) {
  const [viewSheetOpen, setViewSheetOpen] = useState(false)
  const [editSheetOpen, setEditSheetOpen] = useState(false)
  const [jsonEditSheetOpen, setJsonEditSheetOpen] = useState(false)

  const textRef = useRef<HTMLDivElement>(null)
  const [isTruncated, setIsTruncated] = useState(false)

  const normalizedType = normalizeSqlType(params.tableColumn?.type)
  const isJsonType = JSON_TYPES.has(normalizedType)
  const isSelectType = SELECT_TYPES.has(normalizedType)
  const isDateType = DATE_TYPES.has(normalizedType)
  const isObjectValue =
    typeof params.value === "object" && params.value !== null
  const isStringValue = typeof params.value === "string"

  useEffect(() => {
    const el = textRef.current
    if (!el) return
    const check = () => setIsTruncated(el.scrollWidth > el.clientWidth)
    check()
    const observer = new ResizeObserver(check)
    observer.observe(el)
    return () => observer.disconnect()
  }, [params.value])

  const handleEditClick = useCallback(() => {
    if (isJsonType) {
      setJsonEditSheetOpen(true)
      return
    }
    if (params.node.rowIndex != null && params.column) {
      params.api.startEditingCell({
        rowIndex: params.node.rowIndex,
        colKey: params.column.getColId(),
      })
    }
  }, [isJsonType, params.api, params.node.rowIndex, params.column])

  return (
    <div className="group flex h-full w-full items-center">
      <div ref={textRef} className="flex-1 min-w-0 overflow-hidden">
        <CellDisplay value={params.value} column={params.tableColumn} />
      </div>
      <div className="shrink-0 hidden group-hover:flex items-center">
        {/* Text values (non-select): NotebookPen always, Pencil only when not truncated */}
        {isStringValue && !isSelectType && !isDateType && (
          <button
            type="button"
            onClick={() => setEditSheetOpen(true)}
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <NotebookPen className="size-3" />
          </button>
        )}
        {isStringValue && !isSelectType && !isDateType && !isTruncated && (
          <button
            type="button"
            onClick={handleEditClick}
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <Pencil className="size-3" />
          </button>
        )}
        {/* JSON/object values: Eye (view) + Pencil (edit) */}
        {isObjectValue && (
          <button
            type="button"
            onClick={() => setViewSheetOpen(true)}
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <Eye className="size-3" />
          </button>
        )}
        {/* Non-text types or select types: Pencil for inline edit */}
        {(!isStringValue || isSelectType || isDateType) && (
          <button
            type="button"
            onClick={handleEditClick}
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <Pencil className="size-3" />
          </button>
        )}
      </div>

      {/* View Sheet (read-only, JSON only) */}
      <Sheet open={viewSheetOpen} onOpenChange={setViewSheetOpen}>
        <SheetContent side="right" className="sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>View JSON</SheetTitle>
          </SheetHeader>
          <div className="mt-4">
            <JsonViewWithControls
              src={params.value}
              defaultExpanded
              defaultTab="nested"
            />
          </div>
        </SheetContent>
      </Sheet>

      {/* Edit Sheet (text — NotebookPen) */}
      <TextEditSheet
        open={editSheetOpen}
        onOpenChange={setEditSheetOpen}
        value={String(params.value ?? "")}
        onSave={(newText) => {
          if (params.column) {
            params.node.setDataValue(params.column.getColId(), newText)
          }
          setEditSheetOpen(false)
        }}
      />

      {/* JSON Edit Sheet (pencil on JSON cells) */}
      <JsonEditSheet
        open={jsonEditSheetOpen}
        onOpenChange={setJsonEditSheetOpen}
        value={params.value}
        onSave={(parsed) => {
          if (params.column) {
            params.node.setDataValue(params.column.getColId(), parsed)
          }
          setJsonEditSheetOpen(false)
        }}
      />
    </div>
  )
}

function TextEditSheet({
  open,
  onOpenChange,
  value,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  value: string
  onSave: (value: string) => void
}) {
  const [localValue, setLocalValue] = useState(value)

  // Reset local value when sheet opens
  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (next) {
        setLocalValue(value)
      }
      onOpenChange(next)
    },
    [value, onOpenChange]
  )

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent side="right" className="sm:max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Edit text</SheetTitle>
        </SheetHeader>
        <div className="mt-4">
          <SimpleEditor
            value={localValue}
            onChange={setLocalValue}
            editable={true}
            showToolbar={true}
            autoFocus
          />
        </div>
        <SheetFooter className="mt-4">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => onSave(localValue)}>Save</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

function jsonSheetLinter(view: EditorView): Diagnostic[] {
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

function JsonEditSheet({
  open,
  onOpenChange,
  value,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  value: unknown
  onSave: (parsed: unknown) => void
}) {
  const serialized =
    typeof value === "string"
      ? value
      : value === null || value === undefined
        ? ""
        : JSON.stringify(value, null, 2)

  const [localValue, setLocalValue] = useState(serialized)
  const [error, setError] = useState<string | null>(null)

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (next) {
        setLocalValue(serialized)
        setError(null)
      }
      onOpenChange(next)
    },
    [serialized, onOpenChange]
  )

  const handleSaveRef = useRef<() => void>(() => {})

  const handleSave = useCallback(() => {
    const trimmed = localValue.trim()
    if (trimmed === "") {
      onSave(null)
      return
    }
    try {
      const parsed = JSON.parse(trimmed)
      setError(null)
      onSave(parsed)
    } catch {
      setError("Invalid JSON")
    }
  }, [localValue, onSave])

  handleSaveRef.current = handleSave

  const extensions = useMemo(
    () => [
      json(),
      lintGutter(),
      linter(jsonSheetLinter),
      history(),
      bracketMatching(),
      closeBrackets(),
      keymap.of([
        {
          key: "Mod-Enter",
          run: () => {
            handleSaveRef.current()
            return true
          },
          preventDefault: true,
        },
      ]),
      EditorView.theme({
        ".cm-content": { fontFamily: "monospace", fontSize: "12px" },
        ".cm-scroller": { maxHeight: "60vh", overflow: "auto" },
      }),
    ],
    []
  )

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent side="right" className="sm:max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Edit JSON</SheetTitle>
        </SheetHeader>
        <div className="mt-4 space-y-2">
          <CodeMirror
            value={localValue}
            onChange={(val) => {
              setLocalValue(val)
              if (error) {
                try {
                  JSON.parse(val)
                  setError(null)
                } catch {
                  // keep error
                }
              }
            }}
            height="auto"
            extensions={extensions}
            theme="light"
            autoFocus
            basicSetup={{
              lineNumbers: true,
              foldGutter: true,
              highlightActiveLine: true,
              bracketMatching: false,
              closeBrackets: false,
              history: false,
              defaultKeymap: true,
              syntaxHighlighting: true,
              autocompletion: false,
            }}
            className="min-h-[200px] overflow-auto rounded-md border font-mono text-xs"
          />
          {error && <p className="text-xs text-destructive">{error}</p>}
          <p className="text-xs text-muted-foreground">
            Cmd/Ctrl+Enter to save
          </p>
        </div>
        <SheetFooter className="mt-4">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave}>Save</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
