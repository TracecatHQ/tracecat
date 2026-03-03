"use client"

import type {
  CasePriority,
  CaseReadMinimal,
  CaseSearchOrderBy,
  CaseSearchRequest,
  CaseSeverity,
  CaseStatus,
} from "@/client"
import { casesSearchCases } from "@/client"
import {
  type CursorPaginationParams,
  type CursorPaginationResponse,
  useCursorPagination,
} from "./use-cursor-pagination"

// Convenience hook for cases specifically
export interface UseCasesPaginationParams {
  workspaceId: string
  limit?: number
  searchTerm?: string | null
  status?: CaseStatus[] | null
  priority?: CasePriority[] | null
  severity?: CaseSeverity[] | null
  assigneeIds?: string[] | null
  tags?: string[] | null
}

function toCaseOrderBy(
  orderBy: string | null | undefined
): CaseSearchOrderBy | undefined {
  switch (orderBy) {
    case "created_at":
    case "updated_at":
    case "priority":
    case "severity":
    case "status":
    case "tasks":
      return orderBy
    default:
      return undefined
  }
}

export function useCasesPagination({
  workspaceId,
  limit,
  searchTerm,
  status,
  priority,
  severity,
  assigneeIds,
  tags,
}: UseCasesPaginationParams) {
  // Wrapper function to adapt the API response to our generic interface
  const adaptedCasesSearchCases = async (
    params: CursorPaginationParams
  ): Promise<CursorPaginationResponse<CaseReadMinimal>> => {
    const requestBody: CaseSearchRequest = {
      search_term: searchTerm,
      status: status && status.length ? status : null,
      priority: priority && priority.length ? priority : null,
      severity: severity && severity.length ? severity : null,
      assignee_id: assigneeIds && assigneeIds.length ? assigneeIds : null,
      tags,
      limit: params.limit,
      cursor: params.cursor,
      reverse: params.reverse,
      order_by: toCaseOrderBy(params.orderBy),
      sort: params.sort,
    }

    const response = await casesSearchCases({
      workspaceId: params.workspaceId,
      requestBody,
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

  return useCursorPagination<CaseReadMinimal, CursorPaginationParams>({
    workspaceId,
    limit,
    queryKey: [
      "cases",
      "paginated",
      workspaceId,
      searchTerm ?? null,
      status && status.length ? [...status].sort().join(",") : null,
      priority && priority.length ? [...priority].sort().join(",") : null,
      severity && severity.length ? [...severity].sort().join(",") : null,
      assigneeIds && assigneeIds.length
        ? [...assigneeIds].sort().join(",")
        : null,
      tags ? [...tags].sort().join(",") : null,
    ],
    queryFn: adaptedCasesSearchCases,
  })
}
