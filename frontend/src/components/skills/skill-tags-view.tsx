"use client"

import { TagIcon } from "lucide-react"
import type { TagUpdate } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
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
import { useSkillTagCatalog } from "@/hooks/use-skill-tags"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Render the workspace skill tag catalog.
 *
 * @returns The skill tags management view.
 */
export function SkillTagsView() {
  const workspaceId = useWorkspaceId()
  const canUpdateSkill = useScopeCheck("agent:update") === true
  const canDeleteSkill = useScopeCheck("agent:delete") === true
  const {
    skillTags,
    skillTagsIsLoading,
    skillTagsError,
    deleteSkillTag,
    deleteSkillTagIsPending,
    updateSkillTag,
    updateSkillTagIsPending,
  } = useSkillTagCatalog(workspaceId)

  const handleDeleteTag = async (tagId: string) => {
    await deleteSkillTag({ tagId })
  }

  const handleUpdateTag = async (tagId: string, params: TagUpdate) => {
    await updateSkillTag({ tagId, ...params })
  }

  if (skillTagsIsLoading) {
    return <CenteredSpinner />
  }

  if (skillTagsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading tags: ${skillTagsError.message}`}
      />
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        {!skillTags || skillTags.length === 0 ? (
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
              tags={skillTags}
              onDeleteTag={handleDeleteTag}
              onUpdateTag={handleUpdateTag}
              isDeleting={deleteSkillTagIsPending}
              isUpdating={updateSkillTagIsPending}
              canUpdateTag={canUpdateSkill}
              canDeleteTag={canDeleteSkill}
              resourceLabel="skills"
            />
          </div>
        )}
      </div>
    </div>
  )
}
