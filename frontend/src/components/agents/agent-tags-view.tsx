"use client"

import { TagIcon } from "lucide-react"
import type { TagUpdate } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { TagCatalogTable } from "@/components/tags/tag-catalog-table"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { useAgentTagCatalog } from "@/hooks/use-agent-presets"
import { useWorkspaceId } from "@/providers/workspace-id"

export function AgentTagsView() {
  const workspaceId = useWorkspaceId()

  const {
    agentTags,
    agentTagsIsLoading,
    agentTagsError,
    deleteAgentTag,
    deleteAgentTagIsPending,
    updateAgentTag,
    updateAgentTagIsPending,
  } = useAgentTagCatalog(workspaceId)

  const handleDeleteTag = async (tagId: string) => {
    await deleteAgentTag({ tagId })
  }

  const handleUpdateTag = async (tagId: string, params: TagUpdate) => {
    await updateAgentTag({ tagId, ...params })
  }

  if (agentTagsIsLoading) {
    return <CenteredSpinner />
  }

  if (agentTagsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading tags: ${agentTagsError.message}`}
      />
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        {!agentTags || agentTags.length === 0 ? (
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
            <TagCatalogTable
              tags={agentTags}
              onDeleteTag={handleDeleteTag}
              onUpdateTag={handleUpdateTag}
              isDeleting={deleteAgentTagIsPending}
              isUpdating={updateAgentTagIsPending}
              resourceLabel="agents"
            />
          </div>
        )}
      </div>
    </div>
  )
}
