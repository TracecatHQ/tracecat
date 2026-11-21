"use client"

import type { SecretReadMinimal } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { SecretReferenceForm } from "@/components/workspaces/secret-reference-form"
import { useSecretReference } from "@/hooks/use-secret-stores"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Edit an existing external reference using metadata, never remote secret values. */
export function EditSecretReferenceDialog({
  secret,
  onClose,
}: {
  secret: SecretReadMinimal
  onClose: () => void
}) {
  const workspaceId = useWorkspaceId()
  const { data, isLoading, error } = useSecretReference(
    workspaceId,
    secret.id,
    secret.environment
  )

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Edit secret</DialogTitle>
          <DialogDescription>
            Update the secret reference and key mapping. Values stay in the
            external secret store.
          </DialogDescription>
        </DialogHeader>
        {(isLoading || error) && (
          <DialogBody>
            {isLoading && <CenteredSpinner />}
            {error && (
              <Alert variant="destructive">
                <AlertTitle>Could not load secret reference</AlertTitle>
                <AlertDescription>
                  Close and reopen this dialog to try again.
                </AlertDescription>
              </Alert>
            )}
          </DialogBody>
        )}
        {data && !error && (
          <SecretReferenceForm secret={data} onSaved={onClose} />
        )}
      </DialogContent>
    </Dialog>
  )
}
