import type { GridApi } from "ag-grid-community"
import type React from "react"

export function handleGridKeyDown(
  e: React.KeyboardEvent,
  gridApi: GridApi | null
) {
  if (!gridApi) return

  const isCtrlOrCmd = e.ctrlKey || e.metaKey

  if (isCtrlOrCmd && e.key === "c") {
    e.preventDefault()
    handleCopy(gridApi)
  } else if (isCtrlOrCmd && e.key === "v") {
    e.preventDefault()
    handlePaste(gridApi)
  }
}

function handleCopy(gridApi: GridApi) {
  const selectedRows = gridApi.getSelectedRows()

  if (selectedRows.length > 0) {
    // Copy all selected rows as TSV
    const allColumns = gridApi.getColumns()
    if (!allColumns) return

    const dataColumns = allColumns.filter((col) => {
      const colId = col.getColId()
      return (
        colId !== "checkbox" && colId !== "rowNumber" && colId !== "actions"
      )
    })

    const header = dataColumns.map((col) => col.getColId()).join("\t")
    const rows = selectedRows.map((row) =>
      dataColumns
        .map((col) => {
          const val = row[col.getColId()]
          if (val === null || val === undefined) return ""
          if (typeof val === "object") return JSON.stringify(val)
          return String(val)
        })
        .join("\t")
    )

    navigator.clipboard.writeText([header, ...rows].join("\n"))
    return
  }

  // Copy focused cell value
  const focusedCell = gridApi.getFocusedCell()
  if (!focusedCell) return

  const rowNode = gridApi.getDisplayedRowAtIndex(focusedCell.rowIndex)
  if (!rowNode) return

  const colId = focusedCell.column.getColId()
  const value = rowNode.data[colId]

  if (value === null || value === undefined) {
    navigator.clipboard.writeText("")
  } else if (typeof value === "object") {
    navigator.clipboard.writeText(JSON.stringify(value))
  } else {
    navigator.clipboard.writeText(String(value))
  }
}

async function handlePaste(gridApi: GridApi) {
  const focusedCell = gridApi.getFocusedCell()
  if (!focusedCell) return

  const rowNode = gridApi.getDisplayedRowAtIndex(focusedCell.rowIndex)
  if (!rowNode) return

  const colId = focusedCell.column.getColId()
  // Don't paste into non-editable columns
  if (colId === "checkbox" || colId === "rowNumber" || colId === "actions") {
    return
  }

  try {
    const text = await navigator.clipboard.readText()
    rowNode.setDataValue(colId, text)
  } catch {
    // Clipboard access denied - ignore silently
  }
}
