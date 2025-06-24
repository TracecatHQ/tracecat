"use client"

import type React from "react"
import type { WorkflowReadMinimal } from "@/client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { useWorkflowManager } from "@/lib/hooks"

export function DeleteWorkflowAlertDialog({
  selectedWorkflow,
  setSelectedWorkflow,
  children,
}: React.PropsWithChildren<{
  selectedWorkflow: WorkflowReadMinimal | null
  setSelectedWorkflow: (selectedSecret: WorkflowReadMinimal | null) => void
}>) {
  const { deleteWorkflow } = useWorkflowManager()
  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedWorkflow(null)
        }
      }}
    >
      {children}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete workflow</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this workflow? This action cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (selectedWorkflow) {
                console.log("Deleting workflow", selectedWorkflow)
                await deleteWorkflow(selectedWorkflow.id)
              }
              setSelectedWorkflow(null)
            }}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export const DeleteWorkflowAlertDialogTrigger = AlertDialogTrigger
