"use client"

import type { ApiError, CaseLinkedTableRead } from "@/client"
import {
  casesBatchLinkCaseRows,
  casesBatchUnlinkCaseRows,
  casesListCaseLinkedTables,
} from "@/client"
import { invalidateCaseActivityQueries } from "@/lib/cases/invalidation"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

const CASE_ROW_BATCH_SIZE = 200 // mirrors backend MAX_CASE_ROW_BATCH_SIZE

const EMPTY_LINKED_TABLES: CaseLinkedTableRead[] = []

/** The case a rows hook operates on. */
export interface CaseRowsScope {
  caseId: string
  workspaceId: string
}

/** Rows of one table to link to or unlink from a case. */
export interface CaseRowsBatch {
  tableId: string
  rowIds: string[]
}

/** Summed result of every batch-link request for one call. */
export interface LinkCaseRowsResult {
  linkedCount: number
  alreadyLinkedCount: number
}

/** Summed result of every batch-unlink request for one call. */
export interface UnlinkCaseRowsResult {
  unlinkedCount: number
}

/** Query-key prefix covering the linked-tables summary and every per-table page. */
export function caseRowsQueryKey(caseId: string): string[] {
  return ["case-rows", caseId]
}

function chunkRowIds(rowIds: readonly string[]): string[][] {
  const chunks: string[][] = []
  for (let start = 0; start < rowIds.length; start += CASE_ROW_BATCH_SIZE) {
    chunks.push(rowIds.slice(start, start + CASE_ROW_BATCH_SIZE))
  }
  return chunks
}

/** Tables with rows linked to this case, with per-table counts. */
export function useCaseLinkedTables(
  { caseId, workspaceId }: CaseRowsScope,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const {
    data: linkedTables,
    isLoading: linkedTablesIsLoading,
    error: linkedTablesError,
  } = useQuery<CaseLinkedTableRead[], ApiError>({
    queryKey: [...caseRowsQueryKey(caseId), "tables"],
    queryFn: () => casesListCaseLinkedTables({ caseId, workspaceId }),
    enabled: enabled && Boolean(caseId) && Boolean(workspaceId),
  })

  return {
    linkedTables: linkedTables ?? EMPTY_LINKED_TABLES,
    linkedTablesIsLoading,
    linkedTablesError,
  }
}

/** Link rows from one table to a case (chunked into batches of 200). */
export function useLinkCaseRows({ caseId, workspaceId }: CaseRowsScope) {
  const queryClient = useQueryClient()
  const { mutateAsync: linkCaseRows, isPending: linkCaseRowsIsPending } =
    useMutation<LinkCaseRowsResult, ApiError, CaseRowsBatch>({
      mutationFn: async ({ tableId, rowIds }) => {
        let linkedCount = 0
        let alreadyLinkedCount = 0
        for (const batch of chunkRowIds(rowIds)) {
          const response = await casesBatchLinkCaseRows({
            caseId,
            workspaceId,
            requestBody: { table_id: tableId, row_ids: batch },
          })
          linkedCount += response.linked_count
          alreadyLinkedCount += response.already_linked_count
        }
        return { linkedCount, alreadyLinkedCount }
      },
      // Settled, not success: a later chunk can fail after earlier ones
      // landed, and the panel must still show what did link.
      onSettled: () => {
        queryClient.invalidateQueries({ queryKey: caseRowsQueryKey(caseId) })
        invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
      },
      meta: { suppressErrorToast: true },
    })

  return { linkCaseRows, linkCaseRowsIsPending }
}

/** Unlink rows of one table from a case (chunked into batches of 200). */
export function useUnlinkCaseRows({ caseId, workspaceId }: CaseRowsScope) {
  const queryClient = useQueryClient()
  const { mutateAsync: unlinkCaseRows, isPending: unlinkCaseRowsIsPending } =
    useMutation<UnlinkCaseRowsResult, ApiError, CaseRowsBatch>({
      mutationFn: async ({ tableId, rowIds }) => {
        let unlinkedCount = 0
        for (const batch of chunkRowIds(rowIds)) {
          const response = await casesBatchUnlinkCaseRows({
            caseId,
            workspaceId,
            requestBody: { table_id: tableId, row_ids: batch },
          })
          unlinkedCount += response.unlinked_count
        }
        return { unlinkedCount }
      },
      onSettled: () => {
        queryClient.invalidateQueries({ queryKey: caseRowsQueryKey(caseId) })
        invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
      },
      meta: { suppressErrorToast: true },
    })

  return { unlinkCaseRows, unlinkCaseRowsIsPending }
}
