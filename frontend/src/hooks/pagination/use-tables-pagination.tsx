"use client"

import type { TableRowRead, TablesListRowsData } from "@/client"
import { tablesListRows } from "@/client"
import {
  type CursorPaginationResponse,
  keepPreviousPageFor,
  useCursorPagination,
} from "./use-cursor-pagination"

// Convenience hook for table rows specifically
export interface UseTablesPaginationParams {
  tableId: string
  workspaceId: string
  limit?: number
  enabled?: boolean
  /** Text to search for; sent only together with `searchColumn`. */
  searchTerm?: string | null
  /** Name of the column `searchTerm` is matched against. */
  searchColumn?: string | null
}

export function useTablesPagination({
  tableId,
  workspaceId,
  limit = 50,
  enabled = true,
  searchTerm = null,
  searchColumn = null,
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

  const tableKey = ["rows", "paginated", tableId, workspaceId]

  return useCursorPagination<TableRowRead, TablesListRowsData>({
    workspaceId,
    limit,
    // The search is part of the key, so changing it also resets the cursors.
    queryKey: [...tableKey, searchColumn, searchTerm],
    queryFn: adaptedTablesListRows,
    additionalParams: { tableId, searchTerm, searchColumn },
    enabled,
    // Rows stay up through a new search, sort or page, never a new table.
    placeholderData: keepPreviousPageFor(tableKey),
  })
}
