"use client"

import type {
  CasePriority,
  CaseReadMinimal,
  CaseSearchOrderBy,
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

interface CasesPaginationQueryParams extends CursorPaginationParams {}

const CASE_SEARCH_ORDER_BY_OPTIONS: ReadonlyArray<CaseSearchOrderBy> = [
  "created_at",
  "updated_at",
  "priority",
  "severity",
  "status",
  "tasks",
]

function isCaseSearchOrderBy(
  value: string | null | undefined
): value is CaseSearchOrderBy {
  return (
    typeof value === "string" &&
    CASE_SEARCH_ORDER_BY_OPTIONS.includes(value as CaseSearchOrderBy)
  )
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
    params: CasesPaginationQueryParams
  ): Promise<CursorPaginationResponse<CaseReadMinimal>> => {
    const response = await casesSearchCases({
      workspaceId: params.workspaceId,
      requestBody: {
        limit: params.limit,
        cursor: params.cursor ?? undefined,
        reverse: params.reverse ?? undefined,
        order_by: isCaseSearchOrderBy(params.orderBy)
          ? params.orderBy
          : undefined,
        sort: params.sort ?? undefined,
        search_term: searchTerm ?? undefined,
        status: status && status.length ? status : undefined,
        priority: priority && priority.length ? priority : undefined,
        severity: severity && severity.length ? severity : undefined,
        assignee_id:
          assigneeIds && assigneeIds.length ? assigneeIds : undefined,
        tags: tags && tags.length > 0 ? tags : undefined,
      },
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

  return useCursorPagination<CaseReadMinimal, CasesPaginationQueryParams>({
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
