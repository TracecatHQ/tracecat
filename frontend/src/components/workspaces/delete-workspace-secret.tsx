"use client"

import React from "react"
import { SecretReadMinimal } from "@/client"

import { useWorkspaceSecrets } from "@/lib/hooks"
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

export function DeleteSecretAlertDialog({
  selectedSecret,
  setSelectedSecret,
  children,
}: React.PropsWithChildren<{
  selectedSecret: SecretReadMinimal | null
  setSelectedSecret: (selectedSecret: SecretReadMinimal | null) => void
}>) {
  const { deleteSecretById } = useWorkspaceSecrets()
  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedSecret(null)
        }
      }}
    >
      {children}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete secret</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this secret from the workspace? This
            action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (selectedSecret) {
                console.log("Deleting secret", selectedSecret)
                await deleteSecretById(selectedSecret)
              }
              setSelectedSecret(null)
            }}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export const DeleteSecretAlertDialogTrigger = AlertDialogTrigger
