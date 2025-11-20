"use client"

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
} from "@/components/ui/alert-dialog"

interface OrgWorkspaceSSHKeyDeleteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  sshKey: SecretReadMinimal | null
  onConfirm: () => void | Promise<void>
  isDeleting?: boolean
}

export function OrgWorkspaceSSHKeyDeleteDialog({
  open,
  onOpenChange,
  sshKey,
  onConfirm,
  isDeleting = false,
}: OrgWorkspaceSSHKeyDeleteDialogProps) {
  const handleConfirm = async () => {
    try {
      await onConfirm()
    } catch (error) {
      console.error("Failed to delete SSH key:", error)
      // Keep dialog open so user can retry
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete SSH key</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete the SSH key "{sshKey?.name}"? This
            action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            disabled={isDeleting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {isDeleting ? "Deleting..." : "Delete"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
