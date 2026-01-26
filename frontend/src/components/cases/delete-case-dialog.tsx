"use client"

import type React from "react"
import type { CaseReadMinimal } from "@/client"
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
import { useDeleteCase } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function DeleteCaseAlertDialog({
  selectedCase,
  setSelectedCase,
  children,
}: React.PropsWithChildren<{
  selectedCase: CaseReadMinimal | null
  setSelectedCase: (selectedCase: CaseReadMinimal | null) => void
}>) {
  const workspaceId = useWorkspaceId()
  const { deleteCase } = useDeleteCase({ workspaceId })

  return (
    <AlertDialog
      open={selectedCase !== null}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedCase(null)
        }
      }}
    >
      {children}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete case</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this case? This action cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (selectedCase) {
                console.log("Deleting case", selectedCase)
                await deleteCase(selectedCase.id)
              }
              setSelectedCase(null)
            }}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export const DeleteCaseAlertDialogTrigger = AlertDialogTrigger
