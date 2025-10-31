"use client"

import type { CaseTaskRead } from "@/client"
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
import { useDeleteCaseTask } from "@/lib/hooks"

interface DeleteCaseTaskDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  task: CaseTaskRead | null
  caseId: string
  workspaceId: string
  onDeleteSuccess?: () => void
}

export function DeleteCaseTaskDialog({
  open,
  onOpenChange,
  task,
  caseId,
  workspaceId,
  onDeleteSuccess,
}: DeleteCaseTaskDialogProps) {
  const { deleteTask, deleteTaskIsPending } = useDeleteCaseTask({
    caseId,
    workspaceId,
    taskId: task?.id || "",
  })

  const handleDelete = () => {
    if (!task) return

    deleteTask(undefined, {
      onSuccess: () => {
        onOpenChange(false)
        onDeleteSuccess?.()
      },
    })
  }

  if (!task) return null

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete task</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete "{task.title}"? This action cannot
            be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleteTaskIsPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDelete}
            disabled={deleteTaskIsPending}
            className="bg-red-600 hover:bg-red-700"
          >
            {deleteTaskIsPending ? "Deleting..." : "Delete"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
