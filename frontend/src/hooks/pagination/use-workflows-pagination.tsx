"use client"

import type { WorkflowReadMinimal, WorkflowsListWorkflowsData } from "@/client"
import { workflowsListWorkflows } from "@/client"
import {
  type CursorPaginationResponse,
  useCursorPagination,
} from "./use-cursor-pagination"

export interface UseWorkflowsPaginationParams {
  workspaceId: string
  limit?: number
  tags?: string[] | null
  enabled?: boolean
}

export function useWorkflowsPagination({
  workspaceId,
  limit,
  tags,
  enabled = true,
}: UseWorkflowsPaginationParams) {
  const normalizedTags =
    tags && tags.length > 0 ? [...tags].sort((a, b) => a.localeCompare(b)) : []

  const adaptedWorkflowsList = async (
    params: WorkflowsListWorkflowsData
  ): Promise<CursorPaginationResponse<WorkflowReadMinimal>> => {
    const response = await workflowsListWorkflows({
      ...params,
      tag: normalizedTags.length > 0 ? normalizedTags : null,
    })
    return {
      items: response.items,
      next_cursor: response.next_cursor,
      prev_cursor: response.prev_cursor,
      has_more: response.has_more,
      has_previous: response.has_previous,
      total_estimate: response.total_estimate,
    }
  }

  return useCursorPagination<WorkflowReadMinimal, WorkflowsListWorkflowsData>({
    workspaceId,
    limit,
    enabled,
    queryKey: [
      "workflows",
      "paginated",
      workspaceId,
      normalizedTags.length > 0 ? normalizedTags.join(",") : null,
    ],
    queryFn: adaptedWorkflowsList,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
}
