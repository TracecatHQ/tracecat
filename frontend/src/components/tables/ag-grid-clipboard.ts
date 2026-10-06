import type { GridApi } from "ag-grid-community"
import type React from "react"

/** Options for {@link handleGridKeyDown}. */
export interface GridKeyDownOptions {
  /** Skip paste handling; copy still works. For grids that never write cells. */
  readOnly?: boolean
  /**
   * Whether a row accepts pasted values; omitted means every row does. Lets a
   * writable grid keep paste out of rows it would never persist.
   */
  canPasteRow?: (row: unknown) => boolean
}

/**
 * Handles Ctrl/Cmd+C and Ctrl/Cmd+V on a grid container.
 *
 * Copy writes the selected rows as TSV, or the focused cell when nothing is
 * selected. Paste writes the clipboard text into the focused cell, and only
 * into a cell its column marks editable for that row. Pass `readOnly` on grids
 * that never persist cell edits and `canPasteRow` when only some rows do.
 */
export function handleGridKeyDown(
  e: React.KeyboardEvent,
  gridApi: GridApi | null,
  options: GridKeyDownOptions = {}
) {
  if (!gridApi) return

  // Don't intercept copy/paste when editing a cell — let the editor handle it
  if (gridApi.getEditingCells().length > 0) return

  const isCtrlOrCmd = e.ctrlKey || e.metaKey

  if (isCtrlOrCmd && e.key === "c") {
    e.preventDefault()
    handleCopy(gridApi)
  } else if (isCtrlOrCmd && e.key === "v") {
    if (options.readOnly) return
    e.preventDefault()
    handlePaste(gridApi, options.canPasteRow)
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
  if (!rowNode?.data) return

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

async function handlePaste(
  gridApi: GridApi,
  canPasteRow?: (row: unknown) => boolean
) {
  const focusedCell = gridApi.getFocusedCell()
  if (!focusedCell) return

  const rowNode = gridApi.getDisplayedRowAtIndex(focusedCell.rowIndex)
  if (!rowNode?.data) return
  if (canPasteRow && !canPasteRow(rowNode.data)) return

  const colId = focusedCell.column.getColId()
  // Don't paste into non-editable columns
  if (colId === "checkbox" || colId === "rowNumber" || colId === "actions") {
    return
  }
  // A cell the grid would not let the user type into takes no paste either:
  // JSON columns, for one, are only written through the panel that parses them.
  if (!focusedCell.column.isCellEditable(rowNode)) return

  try {
    const text = await navigator.clipboard.readText()
    rowNode.setDataValue(colId, text)
  } catch {
    // Clipboard access denied - ignore silently
  }
}
