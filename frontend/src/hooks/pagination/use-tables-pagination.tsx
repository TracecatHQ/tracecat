"use client"

import type { TableRowRead, TablesListRowsData } from "@/client"
import { tablesListRows } from "@/client"
import {
  type CursorPaginationResponse,
  useCursorPagination,
} from "./use-cursor-pagination"

// Convenience hook for table rows specifically
export interface UseTablesPaginationParams {
  tableId: string
  workspaceId: string
  limit?: number
}

export function useTablesPagination({
  tableId,
  workspaceId,
  limit = 50,
}: UseTablesPaginationParams) {
  // Wrapper function to adapt the API response to our generic interface
  const adaptedTablesListRows = async (
    params: TablesListRowsData
  ): Promise<CursorPaginationResponse<TableRowRead>> => {
    const response = await tablesListRows(params)
    return {
      items: response.items,
      next_cursor: response.next_cursor,
      prev_cursor: response.prev_cursor,
      has_more: response.has_more,
      has_previous: response.has_previous,
      total_estimate: response.total_estimate,
    }
  }

  return useCursorPagination<TableRowRead, TablesListRowsData>({
    workspaceId,
    limit,
    queryKey: ["rows", "paginated", tableId, workspaceId],
    queryFn: adaptedTablesListRows,
    additionalParams: { tableId },
  })
}
