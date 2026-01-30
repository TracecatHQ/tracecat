import type { CustomCellRendererProps } from "ag-grid-react"
import { Eye, NotebookPen, Pencil } from "lucide-react"
import { useCallback, useState } from "react"
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
import { Textarea } from "@/components/ui/textarea"

interface AgGridCellRendererParams extends CustomCellRendererProps {
  tableColumn: TableColumnRead
}

const JSON_TYPES = new Set(["JSON", "JSONB"])

function normalizeSqlType(rawType?: string) {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

export function AgGridCellRenderer(params: AgGridCellRendererParams) {
  const [viewSheetOpen, setViewSheetOpen] = useState(false)
  const [editSheetOpen, setEditSheetOpen] = useState(false)
  const [jsonEditSheetOpen, setJsonEditSheetOpen] = useState(false)

  const normalizedType = normalizeSqlType(params.tableColumn?.type)
  const isJsonType = JSON_TYPES.has(normalizedType)
  const isObjectValue =
    typeof params.value === "object" && params.value !== null
  const isLongText =
    typeof params.value === "string" && params.value.length > 25
  const isExpandable = isObjectValue || isLongText

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
      <div className="flex-1 min-w-0 overflow-hidden">
        <CellDisplay value={params.value} column={params.tableColumn} />
      </div>
      <div className="shrink-0 hidden group-hover:flex items-center">
        {isExpandable && (
          <button
            type="button"
            onClick={() => setViewSheetOpen(true)}
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <Eye className="size-3" />
          </button>
        )}
        {isLongText && (
          <button
            type="button"
            onClick={() => setEditSheetOpen(true)}
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <NotebookPen className="size-3" />
          </button>
        )}
        <button
          type="button"
          onClick={handleEditClick}
          className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
        >
          <Pencil className="size-3" />
        </button>
      </div>

      {/* View Sheet (read-only) */}
      <Sheet open={viewSheetOpen} onOpenChange={setViewSheetOpen}>
        <SheetContent side="right" className="sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>
              {isObjectValue ? "JSON value" : "Text value"}
            </SheetTitle>
          </SheetHeader>
          <div className="mt-4">
            {isObjectValue ? (
              <JsonViewWithControls src={params.value} defaultExpanded />
            ) : (
              <SimpleEditor
                value={String(params.value ?? "")}
                editable={false}
                showToolbar={false}
              />
            )}
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

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent side="right" className="sm:max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Edit JSON</SheetTitle>
        </SheetHeader>
        <div className="mt-4 space-y-2">
          <Textarea
            value={localValue}
            onChange={(e) => {
              setLocalValue(e.target.value)
              if (error) {
                try {
                  JSON.parse(e.target.value)
                  setError(null)
                } catch {
                  // keep error
                }
              }
            }}
            className="min-h-[200px] font-mono text-xs"
            rows={10}
            autoFocus
          />
          {error && <p className="text-xs text-destructive">{error}</p>}
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
