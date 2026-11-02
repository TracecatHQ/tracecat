import {
  type ApiError,
  type CaseReadMinimal,
  type CasesListLinkedCasesResponse,
  casesListLinkedCases,
} from "@/client"
import { useInfiniteQuery } from "@/lib/query"

const LINKED_CASES_PAGE_SIZE = 25
const EMPTY_CASES: readonly CaseReadMinimal[] = []

/** Identifies one table row, and the case to leave out of its cases. */
export interface LinkedCasesScope {
  workspaceId: string
  tableId: string
  rowId: string
  /** Usually the case the row is being viewed from. */
  excludeCaseId?: string
}

/**
 * Cases that link one table row, newest first, a page at a time. The key sits
 * under `cases`, so any case mutation that invalidates the case lists also
 * refreshes these.
 */
export function useLinkedCases(
  { workspaceId, tableId, rowId, excludeCaseId }: LinkedCasesScope,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const query = useInfiniteQuery<
    CasesListLinkedCasesResponse,
    ApiError,
    { pages: CasesListLinkedCasesResponse[] },
    readonly unknown[],
    string | null
  >({
    queryKey: ["cases", "linked", tableId, rowId, excludeCaseId ?? null],
    queryFn: ({ pageParam }) =>
      casesListLinkedCases({
        workspaceId,
        tableId,
        rowId,
        excludeCaseId,
        limit: LINKED_CASES_PAGE_SIZE,
        cursor: pageParam,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: enabled && Boolean(workspaceId && tableId && rowId),
  })

  const cases = query.data?.pages.flatMap((page) => page.items) ?? EMPTY_CASES

  return {
    linkedCases: cases,
    linkedCasesIsLoading: query.isLoading,
    linkedCasesError: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
  }
}
