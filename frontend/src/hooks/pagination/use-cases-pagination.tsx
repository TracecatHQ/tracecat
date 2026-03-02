"use client"

import type {
  CasePriority,
  CaseReadMinimal,
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

const CASE_ORDER_BY_FIELDS = [
  "created_at",
  "updated_at",
  "priority",
  "severity",
  "status",
  "tasks",
] as const satisfies ReadonlyArray<NonNullable<CaseSearchRequest["order_by"]>>

function isCaseOrderByField(
  value: string | null | undefined
): value is NonNullable<CaseSearchRequest["order_by"]> {
  if (!value) {
    return false
  }
  return CASE_ORDER_BY_FIELDS.includes(
    value as (typeof CASE_ORDER_BY_FIELDS)[number]
  )
}

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
    const response = await casesSearchCases({
      workspaceId: params.workspaceId,
      requestBody: {
        limit: params.limit,
        cursor: params.cursor,
        reverse: params.reverse,
        order_by: isCaseOrderByField(params.orderBy) ? params.orderBy : null,
        sort: params.sort,
        search_term: searchTerm,
        status: status && status.length ? status : null,
        priority: priority && priority.length ? priority : null,
        severity: severity && severity.length ? severity : null,
        assignee_id: assigneeIds && assigneeIds.length ? assigneeIds : null,
        tags,
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
