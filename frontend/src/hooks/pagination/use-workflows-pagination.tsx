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
}

export function useWorkflowsPagination({
  workspaceId,
  limit,
  tags,
}: UseWorkflowsPaginationParams) {
  const normalizedTags = tags && tags.length > 0 ? [...tags].sort() : null

  const adaptedWorkflowsList = async (
    params: WorkflowsListWorkflowsData
  ): Promise<CursorPaginationResponse<WorkflowReadMinimal>> => {
    const response = await workflowsListWorkflows({
      ...params,
      tag: tags && tags.length > 0 ? tags : null,
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
    queryKey: [
      "workflows",
      "paginated",
      workspaceId,
      normalizedTags ? normalizedTags.join(",") : null,
    ],
    queryFn: adaptedWorkflowsList,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
}
