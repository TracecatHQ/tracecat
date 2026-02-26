"use client"

import { TagIcon } from "lucide-react"
import type { TagUpdate } from "@/client"
import { WorkflowTagsTable } from "@/components/dashboard/workflow-tags-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useWorkflowTags } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function WorkflowTagsView() {
  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()

  const {
    tags,
    tagsIsLoading,
    tagsError,
    deleteTag,
    deleteTagIsPending,
    updateTag,
    updateTagIsPending,
  } = useWorkflowTags(workspaceId)

  const handleDeleteTag = async (tagId: string) => {
    await deleteTag({ tagId, workspaceId })
  }

  const handleUpdateTag = async (tagId: string, params: TagUpdate) => {
    await updateTag({
      tagId,
      workspaceId,
      requestBody: params,
    })
  }

  if (workspaceLoading || tagsIsLoading) {
    return <CenteredSpinner />
  }

  if (workspaceError) {
    return (
      <AlertNotification
        level="error"
        message="Error loading workspace info."
      />
    )
  }

  if (!workspace) {
    return <AlertNotification level="error" message="Workspace not found." />
  }

  if (tagsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading tags: ${tagsError.message}`}
      />
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        {!tags || tags.length === 0 ? (
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <TagIcon className="size-6" />
              </EmptyMedia>
              <EmptyTitle>No tags defined yet</EmptyTitle>
              <EmptyDescription>
                Add your first tag using the button in the header
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="space-y-4">
            <WorkflowTagsTable
              tags={tags}
              onDeleteTag={handleDeleteTag}
              onUpdateTag={handleUpdateTag}
              isDeleting={deleteTagIsPending}
              isUpdating={updateTagIsPending}
            />
          </div>
        )}
      </div>
    </div>
  )
}
