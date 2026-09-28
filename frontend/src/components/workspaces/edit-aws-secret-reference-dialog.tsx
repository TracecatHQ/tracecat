"use client"

import type { SecretReadMinimal } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { AwsSecretReferenceForm } from "@/components/workspaces/aws-secret-reference-form"
import { useAwsSecretReference } from "@/hooks/use-secret-stores"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Edit an existing AWS reference using metadata, never remote secret values. */
export function EditAwsSecretReferenceDialog({
  secret,
  onClose,
}: {
  secret: SecretReadMinimal
  onClose: () => void
}) {
  const workspaceId = useWorkspaceId()
  const { data, isLoading, error } = useAwsSecretReference(
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
      <DialogContent className="flex max-h-[85vh] flex-col">
        <DialogTitle>Edit secret</DialogTitle>
        <DialogDescription>
          Update the AWS secret reference and key mapping. Values stay in AWS
          Secrets Manager.
        </DialogDescription>
        {isLoading && <CenteredSpinner />}
        {error && (
          <Alert variant="destructive">
            <AlertTitle>Could not load secret reference</AlertTitle>
            <AlertDescription>
              Close and reopen this dialog to try again.
            </AlertDescription>
          </Alert>
        )}
        {data && !error && (
          <AwsSecretReferenceForm secret={data} onSaved={onClose} />
        )}
      </DialogContent>
    </Dialog>
  )
}
