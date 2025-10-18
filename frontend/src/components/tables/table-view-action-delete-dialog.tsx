"use client"

import type { Row } from "@tanstack/react-table"
import { useParams } from "next/navigation"
import type { TableRowRead } from "@/client"
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

import { useDeleteRow } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function TableViewActionDeleteDialog({
  row,
  open,
  onOpenChange,
}: {
  row: Row<TableRowRead>
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const params = useParams<{ tableId?: string }>()
  const tableId = params?.tableId
  const workspaceId = useWorkspaceId()
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
          <AlertDialogTitle>Delete row</AlertDialogTitle>
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
          <AlertDialogAction variant="destructive" onClick={handleDeleteRow}>
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
