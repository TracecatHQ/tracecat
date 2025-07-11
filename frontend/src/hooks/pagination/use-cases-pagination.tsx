"use client"

import type { CaseReadMinimal, CasesListCasesData } from "@/client"
import { casesListCases } from "@/client"
import {
  type CursorPaginationResponse,
  useCursorPagination,
} from "./use-cursor-pagination"

// Convenience hook for cases specifically
export interface UseCasesPaginationParams {
  workspaceId: string
  limit?: number
}

export function useCasesPagination({
  workspaceId,
  limit,
}: UseCasesPaginationParams) {
  // Wrapper function to adapt the API response to our generic interface
  const adaptedCasesListCases = async (
    params: CasesListCasesData
  ): Promise<CursorPaginationResponse<CaseReadMinimal>> => {
    const response = await casesListCases(params)
    return {
      items: response.items,
      next_cursor: response.next_cursor,
      prev_cursor: response.prev_cursor,
      has_more: response.has_more,
      has_previous: response.has_previous,
      total_estimate: response.total_estimate,
    }
  }

  return useCursorPagination<CaseReadMinimal, CasesListCasesData>({
    workspaceId,
    limit,
    queryKey: ["cases", "paginated", workspaceId],
    queryFn: adaptedCasesListCases,
  })
}
