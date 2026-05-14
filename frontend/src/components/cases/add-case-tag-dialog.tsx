"use client"

import { CreateTagDialog } from "@/components/tags/create-tag-dialog"
import { useCaseTagCatalog } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface AddCaseTagDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function AddCaseTagDialog({
  open,
  onOpenChange,
}: AddCaseTagDialogProps) {
  const workspaceId = useWorkspaceId()
  const { caseTags, createCaseTag } = useCaseTagCatalog(workspaceId, {
    enabled: open,
  })

  return (
    <CreateTagDialog
      open={open}
      onOpenChange={onOpenChange}
      existingTags={caseTags}
      onCreateTag={async (params) => {
        await createCaseTag({
          workspaceId,
          requestBody: params,
        })
      }}
      title="Create new case tag"
      description="Enter a name for your new case tag."
    />
  )
}
