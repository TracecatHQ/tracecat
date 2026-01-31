import type { CustomCellEditorProps } from "ag-grid-react"
import { Check } from "lucide-react"
import { forwardRef, useCallback, useImperativeHandle, useRef } from "react"
import type { TableColumnRead } from "@/client"
import { CellEditor } from "@/components/tables/cell-editors"

interface AgGridCellEditorParams extends CustomCellEditorProps {
  tableColumn: TableColumnRead
}

export const AgGridCellEditor = forwardRef(
  (params: AgGridCellEditorParams, ref) => {
    // Track the latest value in a ref so getValue() always returns the
    // most recent value, even if React hasn't re-rendered yet after
    // onValueChange is called.
    const valueRef = useRef(params.value)
    valueRef.current = params.value

    useImperativeHandle(ref, () => ({
      getValue: () => valueRef.current,
      isCancelAfterEnd: () => false,
    }))

    const handleChange = useCallback(
      (newValue: unknown) => {
        valueRef.current = newValue
        params.onValueChange(newValue)
      },
      [params]
    )

    const handleCommit = useCallback(() => {
      params.stopEditing(false)
    }, [params])

    const handleCancel = useCallback(() => {
      params.stopEditing(true)
    }, [params])

    return (
      <div className="flex size-full items-start bg-background">
        <div className="flex-1 min-w-0">
          <CellEditor
            value={params.value}
            column={params.tableColumn}
            onChange={handleChange}
            onCommit={handleCommit}
            onCancel={handleCancel}
            cellWidth={params.column.getActualWidth()}
          />
        </div>
        <button
          type="button"
          onClick={handleCommit}
          className="flex shrink-0 items-center justify-center size-7 text-muted-foreground hover:text-foreground"
        >
          <Check className="size-3.5" />
        </button>
      </div>
    )
  }
)

AgGridCellEditor.displayName = "AgGridCellEditor"
