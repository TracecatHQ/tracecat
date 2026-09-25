"use client"
import {
  type ApiError,
  type VcsGetBitbucketDataCenterTokenCredentialsStatusResponse,
  type VcsSaveBitbucketDataCenterTokenCredentialsData,
  type VcsSaveBitbucketDataCenterTokenCredentialsResponse,
  vcsDeleteBitbucketDataCenterTokenCredentials,
  vcsGetBitbucketDataCenterTokenCredentialsStatus,
  vcsSaveBitbucketDataCenterTokenCredentials,
} from "@/client"
import {
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"
/** Read credential configuration status without retrieving the token. */
export function useBitbucketDataCenterTokenCredentialsStatus() {
  const {
    data: credentialsStatus,
    isLoading: credentialsStatusIsLoading,
    error: credentialsStatusError,
    refetch: refetchCredentialsStatus,
  } = useQuery<VcsGetBitbucketDataCenterTokenCredentialsStatusResponse>({
    queryKey: ["bitbucket-data-center-token-credentials-status"],
    queryFn: async () =>
      await vcsGetBitbucketDataCenterTokenCredentialsStatus(),
  })

  return {
    credentialsStatus,
    credentialsStatusIsLoading,
    credentialsStatusError,
    refetchCredentialsStatus,
  }
}

/** Save or rotate the organization API token. */
export function useBitbucketDataCenterTokenCredentials() {
  const queryClient = useQueryClient()

  const saveCredentials = useMutation<
    VcsSaveBitbucketDataCenterTokenCredentialsResponse,
    ApiError,
    VcsSaveBitbucketDataCenterTokenCredentialsData["requestBody"]
  >({
    mutationFn: async (data) => {
      return await vcsSaveBitbucketDataCenterTokenCredentials({
        requestBody: data,
      })
    },
    meta: { suppressErrorToast: true },
    onSuccess: () => {
      invalidateBitbucketDataCenterTokenCredentialQueries(queryClient)
    },
  })

  return {
    saveCredentials,
  }
}

/** Disconnect Data Center sync without deleting remote or workspace resources. */
export function useDeleteBitbucketDataCenterTokenCredentials() {
  const queryClient = useQueryClient()

  const deleteCredentials = useMutation<void, ApiError>({
    mutationFn: async () => {
      await vcsDeleteBitbucketDataCenterTokenCredentials()
    },
    onSuccess: () => {
      invalidateBitbucketDataCenterTokenCredentialQueries(queryClient)
    },
    meta: { suppressErrorToast: true },
  })

  return {
    deleteCredentials,
  }
}

function invalidateBitbucketDataCenterTokenCredentialQueries(
  queryClient: QueryClient
) {
  queryClient.invalidateQueries({
    queryKey: ["bitbucket-data-center-token-credentials-status"],
  })
  queryClient.invalidateQueries({
    queryKey: ["workflow-sync-branches"],
  })
  queryClient.invalidateQueries({
    queryKey: ["repository_commits"],
  })
}
