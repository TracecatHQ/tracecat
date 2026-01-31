import type { CustomCellRendererProps } from "ag-grid-react"
import { Eye, NotebookPen, Pencil } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import type { TableColumnRead } from "@/client"
import { CellDisplay } from "@/components/tables/cell-display"
import { useTablePanel } from "@/components/tables/table-panel-context"

interface AgGridCellRendererParams extends CustomCellRendererProps {
  tableColumn: TableColumnRead
}

const JSON_TYPES = new Set(["JSON", "JSONB"])
const SELECT_TYPES = new Set(["SELECT", "MULTI_SELECT"])
const DATE_TYPES = new Set(["DATE", "TIMESTAMP", "TIMESTAMPTZ"])
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

function normalizeSqlType(rawType?: string) {
  if (!rawType) return ""
  const [base] = rawType.toUpperCase().split("(")
  return base.trim()
}

export function AgGridCellRenderer(params: AgGridCellRendererParams) {
  const { openPanel } = useTablePanel()

  const textRef = useRef<HTMLDivElement>(null)
  const [isTruncated, setIsTruncated] = useState(false)

  const normalizedType = normalizeSqlType(params.tableColumn?.type)
  const isJsonType = JSON_TYPES.has(normalizedType)
  const isSelectType = SELECT_TYPES.has(normalizedType)
  const isDateType = DATE_TYPES.has(normalizedType)
  const isNumericType = NUMERIC_TYPES.has(normalizedType)
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

  const setDataValue = useCallback(
    (value: unknown) => {
      if (params.column) {
        params.node.setDataValue(params.column.getColId(), value)
      }
    },
    [params.column, params.node]
  )

  const handleEditClick = useCallback(() => {
    if (isJsonType) {
      openPanel({
        mode: "edit-json",
        value: params.value,
        onSave: setDataValue,
      })
      return
    }
    if (params.node.rowIndex != null && params.column) {
      params.api.startEditingCell({
        rowIndex: params.node.rowIndex,
        colKey: params.column.getColId(),
      })
    }
  }, [
    isJsonType,
    params.api,
    params.node.rowIndex,
    params.column,
    params.value,
    openPanel,
    setDataValue,
  ])

  return (
    <div className="group flex h-full w-full items-center">
      <div ref={textRef} className="flex-1 min-w-0 overflow-hidden">
        <CellDisplay value={params.value} column={params.tableColumn} />
      </div>
      <div className="shrink-0 hidden group-hover:flex items-center">
        {/* Text values (non-select, non-numeric): NotebookPen always, Pencil only when not truncated */}
        {isStringValue && !isSelectType && !isDateType && !isNumericType && (
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
        {isStringValue &&
          !isSelectType &&
          !isDateType &&
          !isNumericType &&
          !isTruncated && (
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
            onClick={() =>
              openPanel({ mode: "view-json", value: params.value })
            }
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <Eye className="size-3" />
          </button>
        )}
        {/* Non-text types, select types, or numeric types: Pencil for inline edit */}
        {(!isStringValue || isSelectType || isDateType || isNumericType) && (
          <button
            type="button"
            onClick={handleEditClick}
            className="flex items-center justify-center size-6 text-muted-foreground hover:text-foreground"
          >
            <Pencil className="size-3" />
          </button>
        )}
      </div>
    </div>
  )
}
