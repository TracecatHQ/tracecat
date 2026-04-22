import type { CustomCellRendererProps } from "ag-grid-react"
import { Eye, NotebookPen } from "lucide-react"
import { useCallback } from "react"
import type { TableColumnRead } from "@/client"
import { CellDisplay } from "@/components/tables/cell-display"
import { useTablePanel } from "@/components/tables/table-panel-context"

interface AgGridCellRendererParams extends CustomCellRendererProps {
  tableColumn: TableColumnRead
}

const JSON_TYPES = new Set(["JSON", "JSONB"])
const TEXT_TYPES = new Set(["TEXT", "VARCHAR", "CHAR", "CITEXT", "BPCHAR"])

function normalizeSqlType(rawType?: string) {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

export function AgGridCellRenderer(params: AgGridCellRendererParams) {
  const { openPanel } = useTablePanel()

  const normalizedType = normalizeSqlType(params.tableColumn?.type)
  const isJsonType = JSON_TYPES.has(normalizedType)
  const isTextType = TEXT_TYPES.has(normalizedType)
  const isStringValue = typeof params.value === "string"

  const setDataValue = useCallback(
    (value: unknown) => {
      if (params.column) {
        params.node.setDataValue(params.column.getColId(), value)
      }
    },
    [params.column, params.node]
  )

  return (
    <div className="group flex h-full w-full items-center">
      <div className="flex-1 min-w-0 overflow-hidden">
        <CellDisplay value={params.value} column={params.tableColumn} />
      </div>
      <div className="shrink-0 hidden group-hover:flex items-center">
        {/* TEXT columns only: open full text editor in side panel */}
        {isStringValue && isTextType && (
          <button
            type="button"
            onClick={() =>
              openPanel({
                mode: "edit-text",
                value: params.value,
                onSave: setDataValue,
              })
            }
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <NotebookPen className="size-3" />
          </button>
        )}
        {/* JSON columns only: Eye (view) + NotebookPen (edit) */}
        {isJsonType && (
          <>
            <button
              type="button"
              onClick={() =>
                openPanel({ mode: "view-json", value: params.value })
              }
              className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
            >
              <Eye className="size-3" />
            </button>
            <button
              type="button"
              onClick={() =>
                openPanel({
                  mode: "edit-json",
                  value: params.value,
                  onSave: setDataValue,
                })
              }
              className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
            >
              <NotebookPen className="size-3" />
            </button>
          </>
        )}
      </div>
    </div>
  )
}
