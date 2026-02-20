"use client"

import type { GridApi } from "ag-grid-community"
import { CopyIcon, PlusIcon, Trash2Icon } from "lucide-react"
import { useParams } from "next/navigation"
import type React from "react"
import { useCallback, useState } from "react"
import type { TableColumnRead, TableRowRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { TableInsertRowDialog } from "@/components/tables/table-insert-row-dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import { useDeleteRow } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface AgGridContextMenuProps {
  children: React.ReactNode
  gridApi: GridApi | null
  columns: TableColumnRead[]
}

export function AgGridContextMenu({
  children,
  gridApi,
  columns,
}: AgGridContextMenuProps) {
  const canModifyTable = useScopeCheck("table:delete")
  const routeParams = useParams<{ tableId?: string }>()
  const tableId = routeParams?.tableId
  const workspaceId = useWorkspaceId()
  const { deleteRow } = useDeleteRow()
  const [deleteDialogRow, setDeleteDialogRow] = useState<TableRowRead | null>(
    null
  )
  const [insertDialogOpen, setInsertDialogOpen] = useState(false)

  const getFocusedRowData = useCallback((): TableRowRead | null => {
    if (!gridApi) return null
    const focusedCell = gridApi.getFocusedCell()
    if (!focusedCell) return null
    const rowNode = gridApi.getDisplayedRowAtIndex(focusedCell.rowIndex)
    return (rowNode?.data as TableRowRead) ?? null
  }, [gridApi])

  const getFocusedCellValue = useCallback((): string => {
    if (!gridApi) return ""
    const focusedCell = gridApi.getFocusedCell()
    if (!focusedCell) return ""
    const rowNode = gridApi.getDisplayedRowAtIndex(focusedCell.rowIndex)
    if (!rowNode?.data) return ""
    const colId = focusedCell.column.getColId()
    const value = rowNode.data[colId]
    if (value === null || value === undefined) return ""
    if (typeof value === "object") return JSON.stringify(value)
    return String(value)
  }, [gridApi])

  const handleCopyCellValue = useCallback(() => {
    const value = getFocusedCellValue()
    navigator.clipboard.writeText(value)
  }, [getFocusedCellValue])

  const handleCopyRowAsTsv = useCallback(() => {
    const row = getFocusedRowData()
    if (!row) return
    const values = columns.map((col) => {
      const val = row[col.name as keyof TableRowRead]
      if (val === null || val === undefined) return ""
      if (typeof val === "object") return JSON.stringify(val)
      return String(val)
    })
    navigator.clipboard.writeText(values.join("\t"))
  }, [getFocusedRowData, columns])

  const handleCopyRowId = useCallback(() => {
    const row = getFocusedRowData()
    if (!row) return
    navigator.clipboard.writeText(row.id)
  }, [getFocusedRowData])

  const handleDeleteRow = async () => {
    if (!deleteDialogRow || !tableId || !workspaceId) return
    try {
      await deleteRow({
        tableId,
        workspaceId,
        rowId: deleteDialogRow.id,
      })
    } catch (error) {
      console.error(error)
    }
    setDeleteDialogRow(null)
  }

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
        <ContextMenuContent className="w-52">
          <ContextMenuItem className="text-xs" onClick={handleCopyCellValue}>
            <CopyIcon className="mr-2 size-3" />
            Copy cell value
          </ContextMenuItem>
          <ContextMenuItem className="text-xs" onClick={handleCopyRowAsTsv}>
            <CopyIcon className="mr-2 size-3" />
            Copy row as TSV
          </ContextMenuItem>
          <ContextMenuItem className="text-xs" onClick={handleCopyRowId}>
            <CopyIcon className="mr-2 size-3" />
            Copy row ID
          </ContextMenuItem>
          {canModifyTable && (
            <>
              <ContextMenuSeparator />
              <ContextMenuItem
                className="text-xs"
                onClick={() => setInsertDialogOpen(true)}
              >
                <PlusIcon className="mr-2 size-3" />
                Insert row
              </ContextMenuItem>
              <ContextMenuItem
                className="text-xs text-destructive"
                onClick={() => {
                  const row = getFocusedRowData()
                  if (row) setDeleteDialogRow(row)
                }}
              >
                <Trash2Icon className="mr-2 size-3" />
                Delete row
              </ContextMenuItem>
            </>
          )}
        </ContextMenuContent>
      </ContextMenu>

      <AlertDialog
        open={!!deleteDialogRow}
        onOpenChange={() => setDeleteDialogRow(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete row</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this row from the table? This
              action cannot be undone.
            </AlertDialogDescription>
            {deleteDialogRow && (
              <AlertDialogDescription>
                Row ID: {deleteDialogRow.id}
              </AlertDialogDescription>
            )}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={handleDeleteRow}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {insertDialogOpen && (
        <TableInsertRowDialog
          open={insertDialogOpen}
          onOpenChange={setInsertDialogOpen}
        />
      )}
    </>
  )
}
