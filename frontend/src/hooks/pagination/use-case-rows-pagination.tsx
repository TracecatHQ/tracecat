"use client"

import type { CasesListCaseRowsData, CaseTableRowRead } from "@/client"
import { casesListCaseRows } from "@/client"
import {
  type CursorPaginationResponse,
  keepPreviousPageFor,
  useCursorPagination,
} from "./use-cursor-pagination"

/** Params for {@link useCaseRowsPagination}. */
export interface UseCaseRowsPaginationParams {
  caseId: string
  tableId: string
  workspaceId: string
  limit?: number
  enabled?: boolean
  /** Text to search for; sent only together with `searchColumn`. */
  searchTerm?: string | null
  /** Name of the row column `searchTerm` is matched against. */
  searchColumn?: string | null
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

/**
 * Cursor-paginate one table's rows linked to a case. The sort held by the
 * cursor hook and the search passed here both reach the API, which applies
 * them across every linked row of the table.
 */
export function useCaseRowsPagination({
  caseId,
  tableId,
  workspaceId,
  limit = 20,
  enabled = true,
  searchTerm = null,
  searchColumn = null,
}: UseCaseRowsPaginationParams) {
  const caseTableKey = ["case-rows", caseId, "table", tableId]

  return useCursorPagination<CaseTableRowRead, CasesListCaseRowsData>({
    workspaceId,
    limit,
    // The search is part of the key, so changing it also resets the cursors.
    queryKey: [...caseTableKey, searchColumn, searchTerm],
    queryFn: listCaseRowsPage,
    additionalParams: { caseId, tableId, searchTerm, searchColumn },
    enabled,
    // Rows stay up through a new search, sort or page, never a new case or
    // table.
    placeholderData: keepPreviousPageFor(caseTableKey),
  })
}
