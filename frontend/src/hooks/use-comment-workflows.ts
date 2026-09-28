"use client"

import { useMemo } from "react"
import {
  foldersListFolders,
  type WorkflowFolderRead,
  type WorkflowReadMinimal,
  workflowsListWorkflows,
} from "@/client"
import { useQuery } from "@/lib/query"

/** A workflow the comment composer can offer as a `/` command. */
export interface CommentWorkflowItem {
  id: string
  title: string
  alias: string | null
  /** Full folder path, e.g. `/Security/Incidents`, or null outside a folder. */
  folderPath: string | null
}

/** Join published workflows to their folders. */
export function toCommentWorkflowItems(
  workflows: WorkflowReadMinimal[],
  folders: WorkflowFolderRead[]
): CommentWorkflowItem[] {
  const folderMap = new Map(folders.map((folder) => [folder.id, folder]))

  // Publishing sets `version`. The trigger consumer fails a comment whose
  // workflow has no published definition, so drafts are never offered.
  const published = workflows.filter((workflow) => workflow.version != null)

  return published.map((workflow) => {
    const folder = workflow.folder_id ? folderMap.get(workflow.folder_id) : null
    return {
      id: workflow.id,
      title: workflow.title,
      alias: workflow.alias ?? null,
      folderPath: folder?.path ?? null,
    }
  })
}

/**
 * Workflows a comment can trigger, as flat selector items.
 *
 * Fetches nothing while `enabled` is false so a composer without the case
 * add-ons entitlement or the `workflow:execute` scope makes no requests.
 */
export function useCommentWorkflows(
  workspaceId: string,
  enabled: boolean
): {
  items: CommentWorkflowItem[]
  isLoading: boolean
} {
  const { data: workflows = [], isLoading: workflowsIsLoading } = useQuery({
    queryKey: ["comment-workflows", workspaceId],
    queryFn: async () => {
      const response = await workflowsListWorkflows({
        workspaceId,
        limit: 0,
      })
      return response.items
    },
    enabled,
    staleTime: 5 * 60 * 1000,
  })
  const { data: folders = [], isLoading: foldersIsLoading } = useQuery({
    queryKey: ["comment-workflow-folders", workspaceId],
    queryFn: async () => await foldersListFolders({ workspaceId }),
    enabled,
    staleTime: 5 * 60 * 1000,
  })

  const items = useMemo(
    () => toCommentWorkflowItems(workflows, folders),
    [folders, workflows]
  )

  return {
    items,
    isLoading: workflowsIsLoading || foldersIsLoading,
  }
}
