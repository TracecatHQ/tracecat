"use client"

import { FileUpIcon } from "lucide-react"
import { useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Button } from "@/components/ui/button"
import { AwsCredentialSyncDialog } from "@/components/workspaces/aws-credential-sync-dialog"

export function AwsCredentialSyncAction() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const canManageAwsCredentialSync =
    useScopeCheck("org:credential-sync:manage") === true

  if (!canManageAwsCredentialSync) {
    return null
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => setDialogOpen(true)}
      >
        <FileUpIcon className="mr-1 h-3.5 w-3.5" />
        AWS sync
      </Button>

      <AwsCredentialSyncDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  )
}
