"use client"

import { useMemo } from "react"
import {
  type CasesListCaseVersionsResponse,
  type CaseVersionCompareRead,
  type CaseVersionField,
  type CaseVersionReadMinimal,
  type CaseVersionRestoreRead,
  casesCompareCaseVersion,
  casesListCaseVersions,
  casesRestoreCaseVersion,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { invalidateCaseActivityQueries } from "@/lib/cases/invalidation"
import {
  getApiErrorDetail,
  retryHandler,
  type TracecatApiError,
} from "@/lib/errors"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"

const CASE_VERSION_PAGE_SIZE = 50

/** Cache-key prefix shared by every version query for one case. */
function caseVersionsQueryKey(workspaceId: string, caseId: string) {
  return ["case-versions", workspaceId, caseId] as const
}

/** Cache key for one field-filtered, cursor-paginated version list. */
function caseVersionListQueryKey(
  workspaceId: string,
  caseId: string,
  field: CaseVersionField | null
) {
  return [
    ...caseVersionsQueryKey(workspaceId, caseId),
    "list",
    field ?? "all",
  ] as const
}

/** Cache key for one selected version's predecessor comparison. */
function caseVersionComparisonQueryKey(
  workspaceId: string,
  caseId: string,
  versionId: string
) {
  return [
    ...caseVersionsQueryKey(workspaceId, caseId),
    "compare",
    versionId,
  ] as const
}

/** Inputs for the cursor-paginated case-version history query. */
export interface UseCaseVersionsOptions {
  workspaceId: string
  caseId: string
  /** Restrict history to one independently versioned case field. */
  field?: CaseVersionField | null
  /** Disable fetching while the owning surface is inactive. */
  enabled?: boolean
}

/**
 * List case versions 50 at a time while preserving the backend's stable
 * newest-first cursor order. Additional pages are fetched only when the host
 * explicitly calls `fetchNextPage`.
 */
export function useCaseVersions({
  workspaceId,
  caseId,
  field = null,
  enabled = true,
}: UseCaseVersionsOptions) {
  const query = useInfiniteQuery<
    CasesListCaseVersionsResponse,
    TracecatApiError
  >({
    queryKey: caseVersionListQueryKey(workspaceId, caseId, field),
    queryFn: async ({ pageParam }) =>
      await casesListCaseVersions({
        workspaceId,
        caseId,
        field: field ?? undefined,
        limit: CASE_VERSION_PAGE_SIZE,
        cursor: typeof pageParam === "string" ? pageParam : undefined,
      }),
    enabled: enabled && Boolean(workspaceId) && Boolean(caseId),
    initialPageParam: null,
    getNextPageParam: (lastPage) => {
      if (lastPage.has_more === false || !lastPage.next_cursor) {
        return undefined
      }
      return lastPage.next_cursor
    },
    retry: retryHandler,
  })

  const versions = useMemo<CaseVersionReadMinimal[]>(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data?.pages]
  )

  return {
    versions,
    versionsIsLoading: query.isLoading,
    versionsError: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    isFetchNextPageError: query.isFetchNextPageError,
    fetchNextPage: query.fetchNextPage,
  }
}

/** Inputs for a lazy case-version predecessor comparison. */
export interface UseCaseVersionComparisonOptions {
  workspaceId: string
  caseId: string
  /** Selected immutable version. A null value keeps the query disabled. */
  versionId?: string | null
  /** Additional host-owned query gate. */
  enabled?: boolean
}

/** Load one selected case version and its immediate same-field predecessor. */
export function useCaseVersionComparison({
  workspaceId,
  caseId,
  versionId,
  enabled = true,
}: UseCaseVersionComparisonOptions) {
  const query = useQuery<CaseVersionCompareRead, TracecatApiError>({
    queryKey: caseVersionComparisonQueryKey(
      workspaceId,
      caseId,
      versionId ?? ""
    ),
    queryFn: async () => {
      if (!versionId) {
        throw new Error("versionId is required to compare a case version")
      }
      return await casesCompareCaseVersion({
        workspaceId,
        caseId,
        versionId,
      })
    },
    enabled:
      enabled && Boolean(workspaceId) && Boolean(caseId) && Boolean(versionId),
    retry: retryHandler,
  })

  return {
    comparison: query.data,
    comparisonIsLoading: query.isLoading,
    comparisonError: query.error,
  }
}

/** Inputs for restoring an immutable case-field version. */
export interface UseRestoreCaseVersionOptions {
  workspaceId: string
  caseId: string
}

/** Mutation variables for restoring one selected case version. */
export interface RestoreCaseVersionVariables {
  versionId: string
}

/** Restore one case-field version and refresh all affected case surfaces. */
export function useRestoreCaseVersion({
  workspaceId,
  caseId,
}: UseRestoreCaseVersionOptions) {
  const queryClient = useQueryClient()
  const mutation = useMutation<
    CaseVersionRestoreRead,
    TracecatApiError,
    RestoreCaseVersionVariables
  >({
    mutationFn: async ({ versionId }) =>
      await casesRestoreCaseVersion({
        workspaceId,
        caseId,
        versionId,
      }),
    onSuccess: (response) => {
      invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
      queryClient.invalidateQueries({
        queryKey: ["cases"],
        exact: false,
      })
      queryClient.invalidateQueries({
        queryKey: caseVersionsQueryKey(workspaceId, caseId),
        exact: false,
      })

      const fieldLabel = response.field === "summary" ? "Title" : "Description"
      toast({
        title: `${fieldLabel} restored`,
        description: `The selected ${fieldLabel.toLowerCase()} version is now current.`,
      })
    },
    onError: (error) => {
      toast({
        title: "Restore failed",
        description:
          getApiErrorDetail(error) ?? "Failed to restore the case version.",
        variant: "destructive",
      })
    },
  })

  return {
    restoreCaseVersion: mutation.mutateAsync,
    restoreCaseVersionIsPending: mutation.isPending,
    restoreCaseVersionError: mutation.error,
  }
}
