"use client"

import type { FormReadMinimal } from "@/client"
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
import { useDeleteForm } from "@/hooks/use-forms"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Confirm deleting a form. Its submissions and activity go with it; the
 * workflow and the runs it already started stay.
 */
export function DeleteFormDialog({
  form,
  open,
  onOpenChange,
}: {
  form: Pick<FormReadMinimal, "id" | "title">
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = useWorkspaceId()
  const { deleteForm, deleteFormIsPending } = useDeleteForm(workspaceId)

  async function handleDelete() {
    try {
      await deleteForm(form.id)
      onOpenChange(false)
    } catch {
      // The mutation already reported the failure; keep the dialog open.
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete form</AlertDialogTitle>
          <AlertDialogDescription>
            Delete {form.title}? Its submissions and activity are deleted with
            it. The workflow and its past runs are not affected. This cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={deleteFormIsPending}
            onClick={(event) => {
              // Close only after the request succeeds.
              event.preventDefault()
              void handleDelete()
            }}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
