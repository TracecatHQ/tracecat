"use client"

import type React from "react"
import type { VariableReadMinimal } from "@/client"
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
import { useWorkspaceVariables } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function DeleteVariableAlertDialog({
  selectedVariable,
  setSelectedVariable,
  children,
}: React.PropsWithChildren<{
  selectedVariable: VariableReadMinimal | null
  setSelectedVariable: (selectedVariable: VariableReadMinimal | null) => void
}>) {
  const workspaceId = useWorkspaceId()
  const { deleteVariableById } = useWorkspaceVariables(workspaceId, {
    listEnabled: false,
  })
  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedVariable(null)
        }
      }}
    >
      {children}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete variable</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this variable from the workspace?
            This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (selectedVariable) {
                console.log("Deleting variable", selectedVariable)
                await deleteVariableById(selectedVariable)
              }
              setSelectedVariable(null)
            }}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export const DeleteVariableAlertDialogTrigger = AlertDialogTrigger
