"use client"

import { useParams } from "next/navigation"
import { TableRowRead } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { Row } from "@tanstack/react-table"

import { useDeleteRow } from "@/lib/hooks"
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

export function TableViewActionDeleteDialog({
  row,
  open,
  onOpenChange,
}: {
  row: Row<TableRowRead>
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { tableId } = useParams<{ tableId?: string }>()
  const { workspaceId } = useWorkspace()
  const { deleteRow } = useDeleteRow()
  if (!tableId || !workspaceId) {
    return null
  }

  const handleDeleteRow = async () => {
    try {
      await deleteRow({
        tableId,
        workspaceId,
        rowId: row.original.id,
      })
      onOpenChange(false)
    } catch (error) {
      console.error(error)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete Row</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this row from the table? This action
            cannot be undone.
          </AlertDialogDescription>
          <AlertDialogDescription>
            Row ID: {row.original.id}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={handleDeleteRow}>
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
