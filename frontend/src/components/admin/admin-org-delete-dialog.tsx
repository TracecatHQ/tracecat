"use client"

import { useState } from "react"
import type { tracecat_ee__admin__organizations__schemas__OrgRead as OrgRead } from "@/client"
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
import { useAdminOrganizations } from "@/hooks/use-admin"

/**
 * Confirmation dialog for deleting an organization. Owns the confirmation
 * input state and the delete mutation. Rendered only when there is an org
 * staged for deletion, so the dialog tree is absent when no delete is in
 * flight (and absent entirely in single-tenant mode, where the trigger
 * for staging an org is hidden).
 */
export function AdminOrgDeleteDialog({
  org,
  open,
  onOpenChange,
}: {
  org: OrgRead
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [confirmation, setConfirmation] = useState("")
  const { deleteOrganization } = useAdminOrganizations({ enabled: false })

  const handleDelete = async () => {
    if (confirmation.trim() !== org.name) {
      return
    }
    try {
      await deleteOrganization({
        orgId: org.id,
        confirmation: confirmation.trim(),
      })
    } catch (error) {
      console.error("Failed to delete organization", error)
    } finally {
      setConfirmation("")
      onOpenChange(false)
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setConfirmation("")
        }
        onOpenChange(next)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete organization</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete the organization &quot;{org.name}
            &quot;? This action cannot be undone and will delete all associated
            data.
          </AlertDialogDescription>
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              Type <span className="font-medium">{org.name}</span> to confirm.
            </p>
            <Input
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              placeholder={org.name}
              autoComplete="off"
            />
          </div>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={handleDelete}
            disabled={confirmation.trim() !== org.name}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
