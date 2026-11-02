"use client"
import {
  type ApiError,
  type VcsGetBitbucketTokenCredentialsStatusResponse,
  type VcsSaveBitbucketTokenCredentialsData,
  type VcsSaveBitbucketTokenCredentialsResponse,
  vcsDeleteBitbucketTokenCredentials,
  vcsGetBitbucketTokenCredentialsStatus,
  vcsSaveBitbucketTokenCredentials,
} from "@/client"
import {
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"
/** Read credential configuration status without retrieving the token. */
export function useBitbucketTokenCredentialsStatus() {
  const {
    data: credentialsStatus,
    isLoading: credentialsStatusIsLoading,
    error: credentialsStatusError,
    refetch: refetchCredentialsStatus,
  } = useQuery<VcsGetBitbucketTokenCredentialsStatusResponse>({
    queryKey: ["bitbucket-token-credentials-status"],
    queryFn: async () => await vcsGetBitbucketTokenCredentialsStatus(),
  })

  return {
    credentialsStatus,
    credentialsStatusIsLoading,
    credentialsStatusError,
    refetchCredentialsStatus,
  }
}

/** Save or rotate the organization API token. */
export function useBitbucketTokenCredentials() {
  const queryClient = useQueryClient()

  const saveCredentials = useMutation<
    VcsSaveBitbucketTokenCredentialsResponse,
    ApiError,
    VcsSaveBitbucketTokenCredentialsData["requestBody"]
  >({
    mutationFn: async (data) => {
      return await vcsSaveBitbucketTokenCredentials({ requestBody: data })
    },
    meta: { suppressErrorToast: true },
    onSuccess: () => {
      invalidateBitbucketTokenCredentialQueries(queryClient)
    },
  })

  return {
    saveCredentials,
  }
}

/** Disconnect Cloud sync without deleting remote or workspace resources. */
export function useDeleteBitbucketTokenCredentials() {
  const queryClient = useQueryClient()

  const deleteCredentials = useMutation<void, ApiError>({
    mutationFn: async () => {
      await vcsDeleteBitbucketTokenCredentials()
    },
    onSuccess: () => {
      invalidateBitbucketTokenCredentialQueries(queryClient)
    },
    meta: { suppressErrorToast: true },
  })

  return {
    deleteCredentials,
  }
}

function invalidateBitbucketTokenCredentialQueries(queryClient: QueryClient) {
  queryClient.invalidateQueries({
    queryKey: ["bitbucket-token-credentials-status"],
  })
  queryClient.invalidateQueries({
    queryKey: ["workflow-sync-branches"],
  })
  queryClient.invalidateQueries({
    queryKey: ["repository_commits"],
  })
}
