"use client"

import type React from "react"
import type { SecretReadMinimal } from "@/client"
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
import { useOrgSecrets } from "@/lib/hooks"

export function DeleteOrgSecretAlertDialog({
  selectedSecret,
  setSelectedSecret,
  children,
}: React.PropsWithChildren<{
  selectedSecret: SecretReadMinimal | null
  setSelectedSecret: (selectedSecret: SecretReadMinimal | null) => void
}>) {
  const { deleteSecretById } = useOrgSecrets()
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
            Are you sure you want to delete this secret from the organization?
            This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (selectedSecret) {
                try {
                  await deleteSecretById(selectedSecret)
                } catch {
                  // The organization secret mutation hook shows a sanitized toast.
                  return
                }
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

export const DeleteOrgSecretAlertDialogTrigger = AlertDialogTrigger
