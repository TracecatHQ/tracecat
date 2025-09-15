"use client"

import type { CaseRecordRead } from "@/client"
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
import { useDeleteCaseRecord, useUnlinkCaseRecord } from "@/lib/hooks"

interface DeleteCaseRecordAlertDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  record: CaseRecordRead | null
  caseId: string
  workspaceId: string
  mode?: "delete" | "unlink"
}

export function DeleteCaseRecordAlertDialog({
  open,
  onOpenChange,
  record,
  caseId,
  workspaceId,
  mode = "delete",
}: DeleteCaseRecordAlertDialogProps) {
  const { deleteCaseRecord, deleteCaseRecordIsPending } = useDeleteCaseRecord({
    caseId,
    workspaceId,
  })

  const { unlinkCaseRecord, unlinkCaseRecordIsPending } = useUnlinkCaseRecord({
    caseId,
    workspaceId,
  })

  const handleAction = async () => {
    if (record) {
      if (mode === "unlink") {
        await unlinkCaseRecord(record.id)
      } else {
        await deleteCaseRecord(record.id)
      }
      onOpenChange(false)
    }
  }

  const isPending =
    mode === "unlink" ? unlinkCaseRecordIsPending : deleteCaseRecordIsPending

  const title = mode === "unlink" ? "Unlink record from case" : "Delete record"
  const description =
    mode === "unlink"
      ? "Are you sure you want to unlink this record from the case? The record data will not be deleted."
      : "Are you sure you want to delete this record? This action cannot be undone."
  const actionText = mode === "unlink" ? "Unlink" : "Delete"
  const pendingText = mode === "unlink" ? "Unlinking..." : "Deleting..."

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={handleAction}
            disabled={isPending}
          >
            {isPending ? pendingText : actionText}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
