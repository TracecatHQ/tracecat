"use client"

import { recordsListRecords } from "@/client"
import { useCursorPagination } from "./use-cursor-pagination"

export interface UseRecordsPaginationParams {
  workspaceId: string
  limit?: number
  entityId?: string | null
}

export function useRecordsPagination({
  workspaceId,
  limit = 20,
  entityId,
}: UseRecordsPaginationParams) {
  return useCursorPagination({
    workspaceId,
    limit,
    queryKey: ["records", workspaceId, entityId ?? null],
    queryFn: async (params) =>
      await recordsListRecords({
        workspaceId: params.workspaceId,
        limit: params.limit,
        cursor: params.cursor || undefined,
        reverse: params.reverse,
        entityId: entityId || undefined,
      }),
    enabled: !!workspaceId,
  })
}
