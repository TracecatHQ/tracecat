import type { CustomCellRendererProps } from "ag-grid-react"
import { Eye, NotebookPen } from "lucide-react"
import { useCallback } from "react"
import type { TableColumnRead } from "@/client"
import { CellDisplay } from "@/components/tables/cell-display"
import { useTablePanel } from "@/components/tables/table-panel-context"

interface AgGridCellRendererParams extends CustomCellRendererProps {
  tableColumn: TableColumnRead
  /**
   * Whether the row's cells may be edited; omitted means every row may. A row
   * that may not swaps the edit buttons for read-only views.
   */
  canEditRow?: (row: unknown) => boolean
  /**
   * Keeps the hover buttons in the layout while hidden, so a grid that sizes
   * columns to their content measures them and hovering never clips the value.
   */
  reserveButtonSpace?: boolean
}

const JSON_TYPES = new Set(["JSON", "JSONB"])
const TEXT_TYPES = new Set(["TEXT", "VARCHAR", "CHAR", "CITEXT", "BPCHAR"])

function normalizeSqlType(rawType?: string) {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

/**
 * Cell renderer for grids hosted under a `TablePanelProvider`: the value, plus
 * hover buttons that open long text and JSON in the cell panel. Editable rows
 * get the editors; the rest get a read-only view of the same value.
 */
export function AgGridCellRenderer(params: AgGridCellRendererParams) {
  const { openPanel } = useTablePanel()
  const canEdit = params.canEditRow?.(params.data) ?? true
  const title = params.tableColumn?.name
  // A row that cannot be edited and holds no value has nothing to open.
  const hasValue = params.value !== null && params.value !== undefined

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
      <div
        className={
          params.reserveButtonSpace
            ? "invisible flex shrink-0 items-center group-hover:visible"
            : "shrink-0 hidden group-hover:flex items-center"
        }
      >
        {/* TEXT columns only: open full text editor in side panel */}
        {isStringValue && isTextType && canEdit && (
          <button
            type="button"
            aria-label="Edit text"
            onClick={() =>
              openPanel({
                mode: "edit-text",
                value: params.value,
                onSave: setDataValue,
                title,
              })
            }
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <NotebookPen className="size-3" />
          </button>
        )}
        {/* Read-only rows: the same full text, without the editor */}
        {isStringValue && isTextType && !canEdit && (
          <button
            type="button"
            aria-label="View text"
            onClick={() =>
              openPanel({ mode: "view-text", value: params.value, title })
            }
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <Eye className="size-3" />
          </button>
        )}
        {/* JSON columns only: Eye (view) + NotebookPen (edit) */}
        {isJsonType && (canEdit || hasValue) && (
          <>
            <button
              type="button"
              aria-label="View JSON"
              onClick={() =>
                openPanel({ mode: "view-json", value: params.value, title })
              }
              className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
            >
              <Eye className="size-3" />
            </button>
            {canEdit && (
              <button
                type="button"
                aria-label="Edit JSON"
                onClick={() =>
                  openPanel({
                    mode: "edit-json",
                    value: params.value,
                    onSave: setDataValue,
                    title,
                  })
                }
                className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
              >
                <NotebookPen className="size-3" />
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
