"use client"

import type { RecordRead } from "@/client"
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
import { useDeleteRecord } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface DeleteRecordAlertDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  record: RecordRead | null
}

export function DeleteRecordAlertDialog({
  open,
  onOpenChange,
  record,
}: DeleteRecordAlertDialogProps) {
  const workspaceId = useWorkspaceId()
  const { deleteRecord, deleteRecordIsPending } = useDeleteRecord()

  const handleDelete = async () => {
    if (record) {
      await deleteRecord({
        workspaceId,
        entityId: record.entity_id,
        recordId: record.id,
      })
      onOpenChange(false)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete record</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this record? This action cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={handleDelete}
            disabled={deleteRecordIsPending}
          >
            {deleteRecordIsPending ? "Deleting..." : "Delete"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
