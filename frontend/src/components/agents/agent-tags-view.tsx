"use client"

import { PlusIcon, TagIcon } from "lucide-react"
import { useState } from "react"
import type { TagUpdate } from "@/client"
import { AddAgentTagDialog } from "@/components/agents/add-agent-tag-dialog"
import { WorkflowTagsTable } from "@/components/dashboard/workflow-tags-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { useAgentTags } from "@/hooks/use-agent-tags"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Workspace-level admin view for agent tag definitions.
 *
 * Reuses WorkflowTagsTable since the underlying tag shape (name, ref, color)
 * is identical to WorkflowTag. Only the data source and CRUD callbacks change.
 */
export function AgentTagsView() {
  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  const [createOpen, setCreateOpen] = useState(false)

  const {
    tags,
    tagsIsLoading,
    tagsError,
    deleteTag,
    deleteTagIsPending,
    updateTag,
    updateTagIsPending,
  } = useAgentTags(workspaceId)

  async function handleDelete(tagId: string) {
    await deleteTag(tagId)
  }

  async function handleUpdate(tagId: string, params: TagUpdate) {
    await updateTag({ tagId, params })
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
        <div className="flex items-center justify-between">
          <div className="space-y-3">
            <h2 className="text-2xl font-semibold tracking-tight">
              Agent tags
            </h2>
            <p className="text-base text-muted-foreground">
              Organize agents with tags. Tags are scoped to this workspace.
            </p>
          </div>
          <Button
            size="sm"
            className="flex items-center gap-2"
            onClick={() => setCreateOpen(true)}
          >
            <PlusIcon className="size-4" />
            New tag
          </Button>
        </div>

        {!tags || tags.length === 0 ? (
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <TagIcon className="size-6" />
              </EmptyMedia>
              <EmptyTitle>No tags defined yet</EmptyTitle>
              <EmptyDescription>
                Create your first agent tag using the button above.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="space-y-4">
            <WorkflowTagsTable
              tags={tags}
              onDeleteTag={handleDelete}
              onUpdateTag={handleUpdate}
              isDeleting={deleteTagIsPending}
              isUpdating={updateTagIsPending}
            />
          </div>
        )}

        <AddAgentTagDialog open={createOpen} onOpenChange={setCreateOpen} />
      </div>
    </div>
  )
}
