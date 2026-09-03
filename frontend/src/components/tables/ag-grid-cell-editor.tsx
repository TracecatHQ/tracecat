import type { CustomCellEditorProps } from "ag-grid-react"
import { forwardRef, useCallback, useImperativeHandle, useRef } from "react"
import type { TableColumnRead } from "@/client"
import { CellEditor } from "@/components/tables/cell-editors"

interface AgGridCellEditorParams extends CustomCellEditorProps {
  tableColumn: TableColumnRead
}

export const AgGridCellEditor = forwardRef(
  (params: AgGridCellEditorParams, ref) => {
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
        <CellEditor
          value={params.value}
          column={params.tableColumn}
          onChange={handleChange}
          onCommit={handleCommit}
          onCancel={handleCancel}
          cellWidth={params.column.getActualWidth()}
        />
      </div>
    )
  }
)

AgGridCellEditor.displayName = "AgGridCellEditor"
