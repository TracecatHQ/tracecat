"use client"

import type { ApiError, CaseLinkedTableRead, CaseTableRowRead } from "@/client"
import {
  casesBatchLinkCaseRows,
  casesBatchUnlinkCaseRows,
  casesListCaseLinkedTables,
  casesListCaseRows,
} from "@/client"
import { invalidateCaseActivityQueries } from "@/lib/cases/invalidation"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

// Mirrors backend MAX_CASE_ROW_BATCH_SIZE, and TRACECAT__LIMIT_CURSOR_MAX for
// the page size the list endpoint accepts.
const CASE_ROW_BATCH_SIZE = 200

const EMPTY_LINKED_TABLES: CaseLinkedTableRead[] = []
const EMPTY_CASE_TABLE_ROWS: CaseTableRowRead[] = []

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

/** A batch-link call failed after some chunks were already committed. */
export class CaseRowsLinkError extends Error {
  readonly linkedCount: number
  readonly alreadyLinkedCount: number
  /** Row IDs in the chunks that committed (linked or already linked) before the failure. */
  readonly committedRowIds: readonly string[]

  constructor({
    linkedCount,
    alreadyLinkedCount,
    committedRowIds,
    cause,
  }: {
    linkedCount: number
    alreadyLinkedCount: number
    committedRowIds: readonly string[]
    cause: unknown
  }) {
    super("Linking rows failed after some rows were committed", { cause })
    this.name = "CaseRowsLinkError"
    this.linkedCount = linkedCount
    this.alreadyLinkedCount = alreadyLinkedCount
    this.committedRowIds = committedRowIds
  }
}

/** Summed result of every batch-unlink request for one call. */
export interface UnlinkCaseRowsResult {
  unlinkedCount: number
}

/** A batch-unlink call failed after some chunks were already committed. */
export class CaseRowsUnlinkError extends Error {
  readonly unlinkedCount: number
  /** Row IDs in the chunks that committed before the failure. */
  readonly committedRowIds: readonly string[]

  constructor({
    unlinkedCount,
    committedRowIds,
    cause,
  }: {
    unlinkedCount: number
    committedRowIds: readonly string[]
    cause: unknown
  }) {
    super("Unlinking rows failed after some rows were committed", { cause })
    this.name = "CaseRowsUnlinkError"
    this.unlinkedCount = unlinkedCount
    this.committedRowIds = committedRowIds
  }
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

/**
 * Every row of one table linked to this case, following the cursor until the
 * server runs out of pages. The case view shows the whole set, so the hook
 * fetches every page rather than exposing pagination controls.
 */
export function useCaseTableRows({
  caseId,
  tableId,
  workspaceId,
}: CaseRowsScope & { tableId: string }) {
  const {
    data: caseTableRows,
    isLoading: caseTableRowsIsLoading,
    error: caseTableRowsError,
  } = useQuery<CaseTableRowRead[], ApiError>({
    queryKey: [...caseRowsQueryKey(caseId), "table", tableId],
    queryFn: async () => {
      const rows: CaseTableRowRead[] = []
      let cursor: string | null = null
      for (;;) {
        const response = await casesListCaseRows({
          caseId,
          workspaceId,
          tableId,
          limit: CASE_ROW_BATCH_SIZE,
          cursor,
        })
        rows.push(...response.items)
        if (!response.has_more || !response.next_cursor) {
          return rows
        }
        cursor = response.next_cursor
      }
    },
    enabled: Boolean(caseId) && Boolean(tableId) && Boolean(workspaceId),
  })

  return {
    caseTableRows: caseTableRows ?? EMPTY_CASE_TABLE_ROWS,
    caseTableRowsIsLoading,
    caseTableRowsError,
  }
}

/**
 * Link rows from one table to a case (chunked into batches of 200).
 *
 * Rejects with a {@link CaseRowsLinkError} carrying whatever the chunks before
 * the failure committed, so callers can report partial success and drop the
 * committed rows from a retry.
 */
export function useLinkCaseRows({ caseId, workspaceId }: CaseRowsScope) {
  const queryClient = useQueryClient()
  const { mutateAsync: linkCaseRows, isPending: linkCaseRowsIsPending } =
    useMutation<LinkCaseRowsResult, CaseRowsLinkError, CaseRowsBatch>({
      mutationFn: async ({ tableId, rowIds }) => {
        let linkedCount = 0
        let alreadyLinkedCount = 0
        const committedRowIds: string[] = []
        for (const batch of chunkRowIds(rowIds)) {
          try {
            const response = await casesBatchLinkCaseRows({
              caseId,
              workspaceId,
              requestBody: { table_id: tableId, row_ids: batch },
            })
            linkedCount += response.linked_count
            alreadyLinkedCount += response.already_linked_count
            committedRowIds.push(...batch)
          } catch (error) {
            throw new CaseRowsLinkError({
              linkedCount,
              alreadyLinkedCount,
              committedRowIds,
              cause: error,
            })
          }
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

/**
 * Unlink rows of one table from a case (chunked into batches of 200).
 *
 * Rejects with a {@link CaseRowsUnlinkError} carrying whatever the chunks
 * before the failure committed, so callers can report partial success and drop
 * the committed rows from a retry.
 */
export function useUnlinkCaseRows({ caseId, workspaceId }: CaseRowsScope) {
  const queryClient = useQueryClient()
  const { mutateAsync: unlinkCaseRows, isPending: unlinkCaseRowsIsPending } =
    useMutation<UnlinkCaseRowsResult, CaseRowsUnlinkError, CaseRowsBatch>({
      mutationFn: async ({ tableId, rowIds }) => {
        let unlinkedCount = 0
        const committedRowIds: string[] = []
        for (const batch of chunkRowIds(rowIds)) {
          try {
            const response = await casesBatchUnlinkCaseRows({
              caseId,
              workspaceId,
              requestBody: { table_id: tableId, row_ids: batch },
            })
            unlinkedCount += response.unlinked_count
            committedRowIds.push(...batch)
          } catch (error) {
            throw new CaseRowsUnlinkError({
              unlinkedCount,
              committedRowIds,
              cause: error,
            })
          }
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
