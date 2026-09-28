"use client"

import type { CasesListCaseRowsData, CaseTableRowRead } from "@/client"
import { casesListCaseRows } from "@/client"
import {
  type CursorPaginationResponse,
  useCursorPagination,
} from "./use-cursor-pagination"

/** Params for {@link useCaseRowsPagination}. */
export interface UseCaseRowsPaginationParams {
  caseId: string
  tableId: string
  workspaceId: string
  limit?: number
  enabled?: boolean
}

/** Adapt one page of case-row links to the generic cursor shape. */
async function listCaseRowsPage(
  params: CasesListCaseRowsData
): Promise<CursorPaginationResponse<CaseTableRowRead>> {
  const response = await casesListCaseRows(params)
  return {
    items: response.items,
    next_cursor: response.next_cursor,
    prev_cursor: response.prev_cursor,
    has_more: response.has_more,
    has_previous: response.has_previous,
    total_estimate: response.total_estimate,
  }
}

/** Cursor-paginate one table's rows linked to a case. */
export function useCaseRowsPagination({
  caseId,
  tableId,
  workspaceId,
  limit = 20,
  enabled = true,
}: UseCaseRowsPaginationParams) {
  return useCursorPagination<CaseTableRowRead, CasesListCaseRowsData>({
    workspaceId,
    limit,
    queryKey: ["case-rows", caseId, "table", tableId],
    queryFn: listCaseRowsPage,
    additionalParams: { caseId, tableId },
    enabled,
  })
}
