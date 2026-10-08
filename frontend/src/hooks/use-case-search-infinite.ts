import { useMemo } from "react"
import { type CaseReadMinimal, casesSearchCases } from "@/client"
import { type InfiniteData, useInfiniteQuery } from "@/lib/query"

type CaseSearchPage = Awaited<ReturnType<typeof casesSearchCases>>

/** Every case, or only cases without a parent. */
export type CaseHierarchyFilter = "all" | "top_level"

/** Options for {@link useCaseSearchInfinite}. */
export interface UseCaseSearchInfiniteOptions {
  workspaceId: string
  searchTerm?: string
  /** List only the sub-cases of this parent. */
  parentId?: string
  hierarchy?: CaseHierarchyFilter
  pageSize: number
  enabled?: boolean
}

/** Cursor-paginated case search that flattens loaded pages into `items`. */
export function useCaseSearchInfinite({
  workspaceId,
  searchTerm,
  parentId,
  hierarchy = "all",
  pageSize,
  enabled = true,
}: UseCaseSearchInfiniteOptions) {
  const query = useInfiniteQuery<
    CaseSearchPage,
    Error,
    InfiniteData<CaseSearchPage, string | null>,
    readonly unknown[],
    string | null
  >({
    queryKey: [
      "cases",
      "search-infinite",
      workspaceId,
      parentId ?? null,
      hierarchy,
      searchTerm ?? "",
      pageSize,
    ],
    queryFn: ({ pageParam }) =>
      casesSearchCases({
        workspaceId,
        parentId,
        hierarchy,
        searchTerm: searchTerm || undefined,
        limit: pageSize,
        cursor: pageParam ?? undefined,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) =>
      lastPage.has_more && lastPage.next_cursor
        ? lastPage.next_cursor
        : undefined,
    enabled,
  })
  const items = useMemo<CaseReadMinimal[]>(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data]
  )
  return { ...query, items }
}
