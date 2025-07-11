"use client"

import { useState } from "react"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

interface OrgWorkspaceDeleteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceName: string
  onConfirm: () => void | Promise<void>
  isDeleting?: boolean
}

export function OrgWorkspaceDeleteDialog({
  open,
  onOpenChange,
  workspaceName,
  onConfirm,
  isDeleting = false,
}: OrgWorkspaceDeleteDialogProps) {
  const [confirmText, setConfirmText] = useState("")
  const isConfirmValid = confirmText === workspaceName

  const handleConfirm = async () => {
    if (isConfirmValid) {
      try {
        await onConfirm()
        setConfirmText("")
      } catch (error) {
        console.error("Failed to delete workspace:", error)
        // Keep dialog open and confirmText intact so user can retry
      }
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle>Delete workspace</AlertDialogTitle>
          <AlertDialogDescription className="space-y-4">
            <p>
              Are you sure you want to delete the workspace{" "}
              <strong>{workspaceName}</strong>? This action cannot be undone.
            </p>
            <p>This will permanently delete:</p>
            <ul className="list-disc list-inside space-y-1 text-sm">
              <li>All workflows in this workspace</li>
              <li>All tables and data</li>
              <li>All cases and case data</li>
              <li>All credentials and integrations</li>
              <li>All member access</li>
            </ul>
            <div className="space-y-2">
              <Label htmlFor="confirm-delete">
                Type <strong>{workspaceName}</strong> to confirm:
              </Label>
              <Input
                id="confirm-delete"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder="Enter workspace name"
                disabled={isDeleting}
              />
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            disabled={!isConfirmValid || isDeleting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {isDeleting ? "Deleting..." : "Delete workspace"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
