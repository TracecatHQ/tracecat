"use client"

import {
  type ExternalGroupMappingCreate,
  type ExternalGroupMappingRead,
  type ScimActivationReviewRead,
  type ScimConnectionRead,
  type ScimConnectionTokenRead,
  type ScimDirectorySummaryRead,
  type ScimListExternalGroupsResponse,
  type ScimListScimMappingsResponse,
  type ScimMappingChangesRequest,
  type ScimReviewRequest,
  scimActivateScimConnection,
  scimApplyScimMappingChanges,
  scimCreateScimMapping,
  scimDeleteScimMapping,
  scimDisconnectScim,
  scimGetScimConnection,
  scimGetScimDirectorySummary,
  scimIssueScimToken,
  scimListExternalGroups,
  scimListScimMappings,
  scimReviewScimActivation,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"

const SCIM_CONNECTION_KEY = ["scim-connection"]
const SCIM_EXTERNAL_GROUPS_KEY = ["scim-external-groups"]
const SCIM_MAPPINGS_KEY = ["scim-mappings"]
const SCIM_DIRECTORY_SUMMARY_KEY = ["scim-directory-summary"]

/** Report a failed SCIM mutation without leaking raw error shapes into the UI. */
function toastScimError(title: string, error: TracecatApiError) {
  if (error.status === 403) {
    toast({
      title: "Permission denied",
      description: "You don't have permission to manage SCIM provisioning.",
      variant: "destructive",
    })
    return
  }
  toast({
    title,
    description: getApiErrorDetail(error) ?? "An unexpected error occurred.",
    variant: "destructive",
  })
}

/**
 * Read and manage the organization's SCIM connection token.
 *
 * The API returns 404 when no connection has ever been issued. That is a
 * legitimate "not configured yet" state rather than a failure, so it resolves
 * to `null` instead of surfacing as a query error.
 */
export function useScimConnection() {
  const queryClient = useQueryClient()

  const {
    data: connection,
    isLoading: connectionIsLoading,
    isFetching: connectionIsFetching,
    error: connectionError,
    refetch: refetchConnection,
  } = useQuery<ScimConnectionRead | null, TracecatApiError>({
    queryKey: SCIM_CONNECTION_KEY,
    queryFn: async () => {
      try {
        return await scimGetScimConnection()
      } catch (error) {
        if ((error as TracecatApiError).status === 404) {
          return null
        }
        throw error
      }
    },
    retry: false,
  })

  const { mutateAsync: issueToken, isPending: issueTokenIsPending } =
    useMutation<ScimConnectionTokenRead, TracecatApiError, void>({
      mutationFn: async () => await scimIssueScimToken(),
      // The API returns the raw token exactly once. Refresh in the background
      // so a failed refetch cannot block the caller from displaying it.
      onSuccess: () => {
        void queryClient.invalidateQueries({ queryKey: SCIM_CONNECTION_KEY })
      },
      onError: (error) => toastScimError("Failed to issue SCIM token", error),
    })

  const { mutateAsync: disconnect, isPending: disconnectIsPending } =
    useMutation<void, TracecatApiError, void>({
      mutationFn: async () => await scimDisconnectScim(),
      onSuccess: async () => {
        await queryClient.invalidateQueries()
        toast({
          title: "SCIM disconnected",
          description: "Group members were kept as manual members.",
        })
      },
      onError: (error) => toastScimError("Failed to disconnect SCIM", error),
    })

  return {
    connection,
    connectionIsLoading,
    connectionIsFetching,
    connectionError,
    refetchConnection,
    issueToken,
    issueTokenIsPending,
    disconnect,
    disconnectIsPending,
  }
}

/** Fetch synced groups in bounded pages as the administrator requests them. */
export function useScimExternalGroups() {
  const query = useInfiniteQuery<
    ScimListExternalGroupsResponse,
    TracecatApiError
  >({
    queryKey: SCIM_EXTERNAL_GROUPS_KEY,
    queryFn: async ({ pageParam }) =>
      await scimListExternalGroups({
        limit: 50,
        cursor: typeof pageParam === "string" ? pageParam : undefined,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  return {
    externalGroups: query.data?.pages.flatMap((page) => page.items),
    externalGroupsIsLoading: query.isLoading,
    externalGroupsError: query.error,
    externalGroupsHasNextPage: query.hasNextPage,
    externalGroupsIsFetchingNextPage: query.isFetchingNextPage,
    fetchNextExternalGroups: query.fetchNextPage,
  }
}

/** Count the users and groups the provider has pushed. */
export function useScimDirectorySummary({ enabled }: { enabled: boolean }) {
  const query = useQuery<ScimDirectorySummaryRead, TracecatApiError>({
    queryKey: SCIM_DIRECTORY_SUMMARY_KEY,
    queryFn: async () => await scimGetScimDirectorySummary(),
    enabled,
  })
  return {
    directorySummary: query.data,
    directorySummaryIsLoading: query.isLoading,
    directorySummaryError: query.error,
  }
}

/**
 * List and edit the rules projecting IdP groups onto Tracecat groups.
 *
 * Creating a mapping is idempotent server-side: a duplicate returns the
 * existing row rather than an error.
 */
export function useScimMappings() {
  const queryClient = useQueryClient()

  const query = useInfiniteQuery<
    ScimListScimMappingsResponse,
    TracecatApiError
  >({
    queryKey: SCIM_MAPPINGS_KEY,
    queryFn: async ({ pageParam }) =>
      await scimListScimMappings({
        limit: 50,
        cursor: typeof pageParam === "string" ? pageParam : undefined,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })

  const { mutateAsync: createMapping, isPending: createMappingIsPending } =
    useMutation<
      ExternalGroupMappingRead,
      TracecatApiError,
      { externalGroupId: string; groupId: string }
    >({
      mutationFn: async ({ externalGroupId, groupId }) =>
        await scimCreateScimMapping({
          requestBody: {
            external_group_id: externalGroupId,
            group_id: groupId,
          },
        }),
      onSuccess: async (mapping) => {
        await queryClient.invalidateQueries({ queryKey: SCIM_MAPPINGS_KEY })
        await queryClient.invalidateQueries({
          queryKey: SCIM_DIRECTORY_SUMMARY_KEY,
        })
        await queryClient.invalidateQueries({ queryKey: ["rbac-groups"] })
        toast({
          title: "Mapping created",
          description: `${mapping.external_group_display_name} now grants membership of ${mapping.group_name}.`,
        })
      },
      onError: (error) => toastScimError("Failed to create mapping", error),
    })

  const { mutateAsync: deleteMapping, isPending: deleteMappingIsPending } =
    useMutation<void, TracecatApiError, string>({
      mutationFn: async (mappingId) =>
        await scimDeleteScimMapping({ mappingId }),
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: SCIM_MAPPINGS_KEY })
        await queryClient.invalidateQueries({
          queryKey: SCIM_DIRECTORY_SUMMARY_KEY,
        })
        await queryClient.invalidateQueries({ queryKey: ["rbac-groups"] })
        toast({
          title: "Mapping removed",
          description:
            "If this was the final mapping, eligible members were retained as manual members. Otherwise, remaining mappings determine membership.",
        })
      },
      onError: (error) => toastScimError("Failed to remove mapping", error),
    })

  const {
    mutateAsync: applyMappingChanges,
    isPending: applyMappingChangesIsPending,
  } = useMutation<void, TracecatApiError, ScimMappingChangesRequest>({
    mutationFn: async (requestBody) =>
      await scimApplyScimMappingChanges({ requestBody }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: SCIM_MAPPINGS_KEY })
      await queryClient.invalidateQueries({
        queryKey: SCIM_DIRECTORY_SUMMARY_KEY,
      })
      await queryClient.invalidateQueries({ queryKey: ["rbac-groups"] })
      toast({ title: "Mapping changes applied" })
    },
    onError: (error) =>
      toastScimError("Failed to apply mapping changes", error),
  })

  return {
    mappings: query.data?.pages.flatMap((page) => page.items),
    mappingsIsLoading: query.isLoading,
    mappingsError: query.error,
    mappingsHasNextPage: query.hasNextPage,
    mappingsIsFetchingNextPage: query.isFetchingNextPage,
    fetchNextMappings: query.fetchNextPage,
    createMapping,
    createMappingIsPending,
    deleteMapping,
    deleteMappingIsPending,
    applyMappingChanges,
    applyMappingChangesIsPending,
  }
}

/** Review without writing, then activate the proposed mappings atomically. */
export function useScimActivation() {
  const queryClient = useQueryClient()
  const review = useMutation<
    ScimActivationReviewRead,
    TracecatApiError,
    ScimReviewRequest
  >({
    mutationFn: (requestBody) => scimReviewScimActivation({ requestBody }),
    onError: (error) => toastScimError("Failed to review SCIM changes", error),
  })
  const activate = useMutation<
    void,
    TracecatApiError,
    ExternalGroupMappingCreate[]
  >({
    mutationFn: (mappings) =>
      scimActivateScimConnection({ requestBody: { mappings } }),
    onSuccess: async () => {
      await queryClient.invalidateQueries()
      toast({
        title: "SCIM activated",
        description: "Eligible directory users have been admitted.",
      })
    },
    onError: (error) => toastScimError("Failed to activate SCIM", error),
  })
  return { review, activate }
}
