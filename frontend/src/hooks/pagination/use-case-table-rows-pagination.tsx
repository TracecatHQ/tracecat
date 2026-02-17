"use client"

import type {
  CaseTableRowRead,
  CaseTableRowsListCaseTableRowsData,
} from "@/client"
import { caseTableRowsListCaseTableRows } from "@/client"
import {
  type CursorPaginationResponse,
  useCursorPagination,
} from "./use-cursor-pagination"

interface UseCaseTableRowsPaginationParams {
  workspaceId: string
  caseId: string
  limit?: number
}

export function useCaseTableRowsPagination({
  workspaceId,
  caseId,
  limit = 20,
}: UseCaseTableRowsPaginationParams) {
  const listCaseTableRows = async (
    params: CaseTableRowsListCaseTableRowsData
  ): Promise<CursorPaginationResponse<CaseTableRowRead>> => {
    const response = await caseTableRowsListCaseTableRows(params)
    return {
      items: response.items ?? [],
      next_cursor: response.next_cursor,
      prev_cursor: response.prev_cursor,
      has_more: response.has_more,
      has_previous: response.has_previous,
      total_estimate: response.total_estimate,
    }
  }

  return useCursorPagination<
    CaseTableRowRead,
    CaseTableRowsListCaseTableRowsData
  >({
    workspaceId,
    limit,
    queryKey: ["case-table-rows", caseId, workspaceId],
    queryFn: listCaseTableRows,
    additionalParams: { caseId },
  })
}
