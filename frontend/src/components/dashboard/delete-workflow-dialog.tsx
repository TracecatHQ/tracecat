"use client"

import React from "react"
import { WorkflowMetadataResponse } from "@/client"

import { useWorkflowManager } from "@/lib/hooks"
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

export function DeleteWorkflowAlertDialog({
  selectedWorkflow,
  setSelectedWorkflow,
  children,
}: React.PropsWithChildren<{
  selectedWorkflow: WorkflowMetadataResponse | null
  setSelectedWorkflow: (selectedSecret: WorkflowMetadataResponse | null) => void
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
          <AlertDialogTitle>Are you sure?</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to remove this secret from the workflow? This
            action cannot be undone.
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
