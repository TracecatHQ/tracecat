"use client"

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
import { useUnlinkCaseTableRow } from "@/lib/hooks"

interface UnlinkCaseTableRowDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  linkId: string | null
  tableName: string
  rowId: string
  caseId: string
  workspaceId: string
  onSuccess?: () => void
}

export function UnlinkCaseTableRowDialog({
  open,
  onOpenChange,
  linkId,
  tableName,
  rowId,
  caseId,
  workspaceId,
  onSuccess,
}: UnlinkCaseTableRowDialogProps) {
  const { unlinkCaseTableRow, unlinkCaseTableRowIsPending } =
    useUnlinkCaseTableRow({ caseId, workspaceId })

  const handleConfirm = async () => {
    if (!linkId) return
    await unlinkCaseTableRow(linkId)
    onOpenChange(false)
    onSuccess?.()
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Unlink table row</AlertDialogTitle>
          <AlertDialogDescription>
            This will remove the link between this case and the table row{" "}
            <span className="font-medium text-foreground">
              {tableName ? `${tableName} / ${rowId}` : rowId}
            </span>
            . The original table entry will remain unchanged.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={unlinkCaseTableRowIsPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            className="bg-rose-600 text-white hover:bg-rose-700"
            disabled={!linkId || unlinkCaseTableRowIsPending}
            onClick={handleConfirm}
          >
            Unlink row
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
