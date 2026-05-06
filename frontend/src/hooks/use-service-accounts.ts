"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ServiceAccountApiKeyCreate,
  type ServiceAccountApiKeyIssueResponse,
  type ServiceAccountApiKeyRead,
  type ServiceAccountCreate,
  type ServiceAccountScopeRead,
  type ServiceAccountUpdate,
  serviceAccountsCreateOrganizationServiceAccount,
  serviceAccountsCreateOrganizationServiceAccountApiKey,
  serviceAccountsCreateWorkspaceServiceAccount,
  serviceAccountsCreateWorkspaceServiceAccountApiKey,
  serviceAccountsDisableOrganizationServiceAccount,
  serviceAccountsDisableWorkspaceServiceAccount,
  serviceAccountsEnableOrganizationServiceAccount,
  serviceAccountsEnableWorkspaceServiceAccount,
  serviceAccountsListOrganizationServiceAccountApiKeys,
  serviceAccountsListOrganizationServiceAccountScopes,
  serviceAccountsListOrganizationServiceAccounts,
  serviceAccountsListWorkspaceServiceAccountApiKeys,
  serviceAccountsListWorkspaceServiceAccountScopes,
  serviceAccountsListWorkspaceServiceAccounts,
  serviceAccountsRevokeOrganizationServiceAccountApiKey,
  serviceAccountsRevokeWorkspaceServiceAccountApiKey,
  serviceAccountsUpdateOrganizationServiceAccount,
  serviceAccountsUpdateWorkspaceServiceAccount,
} from "@/client"

function organizationServiceAccountsQueryKey() {
  return ["organization-service-accounts"] as const
}

function organizationServiceAccountApiKeysQueryKey(serviceAccountId: string) {
  return [
    "organization-service-accounts",
    serviceAccountId,
    "api-keys",
  ] as const
}

function workspaceServiceAccountsQueryKey(workspaceId: string) {
  return ["workspace-service-accounts", workspaceId] as const
}

function workspaceServiceAccountApiKeysQueryKey(
  workspaceId: string,
  serviceAccountId: string
) {
  return [
    ...workspaceServiceAccountsQueryKey(workspaceId),
    serviceAccountId,
    "api-keys",
  ] as const
}

export function useOrganizationServiceAccounts({
  enabled = true,
}: {
  enabled?: boolean
} = {}) {
  const queryClient = useQueryClient()

  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: organizationServiceAccountsQueryKey(),
    queryFn: async () =>
      await serviceAccountsListOrganizationServiceAccounts({ limit: 100 }),
    enabled,
  })

  function invalidate() {
    return queryClient.invalidateQueries({
      queryKey: organizationServiceAccountsQueryKey(),
    })
  }

  function invalidateApiKeys(serviceAccountId: string) {
    return queryClient.invalidateQueries({
      queryKey: organizationServiceAccountApiKeysQueryKey(serviceAccountId),
    })
  }

  const { mutateAsync: createServiceAccount, isPending: createPending } =
    useMutation({
      mutationFn: async (
        requestBody: ServiceAccountCreate
      ): Promise<ServiceAccountApiKeyIssueResponse> =>
        await serviceAccountsCreateOrganizationServiceAccount({ requestBody }),
      onSuccess: invalidate,
    })

  const { mutateAsync: updateServiceAccount, isPending: updatePending } =
    useMutation({
      mutationFn: async (params: {
        serviceAccountId: string
        requestBody: ServiceAccountUpdate
      }) =>
        await serviceAccountsUpdateOrganizationServiceAccount({
          serviceAccountId: params.serviceAccountId,
          requestBody: params.requestBody,
        }),
      onSuccess: invalidate,
    })

  const { mutateAsync: disableServiceAccount, isPending: disablePending } =
    useMutation({
      mutationFn: async (serviceAccountId: string) =>
        await serviceAccountsDisableOrganizationServiceAccount({
          serviceAccountId,
        }),
      onSuccess: invalidate,
    })

  const { mutateAsync: enableServiceAccount, isPending: enablePending } =
    useMutation({
      mutationFn: async (serviceAccountId: string) =>
        await serviceAccountsEnableOrganizationServiceAccount({
          serviceAccountId,
        }),
      onSuccess: invalidate,
    })

  const { mutateAsync: issueApiKey, isPending: issueApiKeyPending } =
    useMutation({
      mutationFn: async (params: {
        serviceAccountId: string
        requestBody: ServiceAccountApiKeyCreate
      }) =>
        await serviceAccountsCreateOrganizationServiceAccountApiKey({
          serviceAccountId: params.serviceAccountId,
          requestBody: params.requestBody,
        }),
      onSuccess: async (_result, variables) => {
        await invalidate()
        await invalidateApiKeys(variables.serviceAccountId)
      },
    })

  const { mutateAsync: revokeApiKey, isPending: revokeApiKeyPending } =
    useMutation({
      mutationFn: async (params: {
        serviceAccountId: string
        apiKeyId: string
      }) =>
        await serviceAccountsRevokeOrganizationServiceAccountApiKey({
          serviceAccountId: params.serviceAccountId,
          apiKeyId: params.apiKeyId,
        }),
      onSuccess: async (_result, variables) => {
        await invalidate()
        await invalidateApiKeys(variables.serviceAccountId)
      },
    })

  return {
    serviceAccounts: response?.items ?? [],
    nextCursor: response?.next_cursor ?? null,
    isLoading,
    error,
    createServiceAccount,
    createPending,
    updateServiceAccount,
    updatePending,
    disableServiceAccount,
    disablePending,
    enableServiceAccount,
    enablePending,
    issueApiKey,
    issueApiKeyPending,
    revokeApiKey,
    revokeApiKeyPending,
  }
}

export function useOrganizationServiceAccountApiKeys(
  serviceAccountId: string | null
) {
  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: serviceAccountId
      ? organizationServiceAccountApiKeysQueryKey(serviceAccountId)
      : ["organization-service-accounts", "no-service-account", "api-keys"],
    queryFn: async () =>
      await serviceAccountsListOrganizationServiceAccountApiKeys({
        serviceAccountId: serviceAccountId!,
        limit: 100,
      }),
    enabled: Boolean(serviceAccountId),
  })

  return {
    apiKeys: (response?.items ?? []) as ServiceAccountApiKeyRead[],
    nextCursor: response?.next_cursor ?? null,
    isLoading,
    error,
  }
}

export function useOrganizationServiceAccountScopes({
  enabled = true,
}: {
  enabled?: boolean
} = {}) {
  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["organization-service-account-scopes"],
    queryFn: async () =>
      await serviceAccountsListOrganizationServiceAccountScopes(),
    enabled,
  })

  return {
    scopes: (response?.items ?? []) as ServiceAccountScopeRead[],
    isLoading,
    error,
  }
}

export function useWorkspaceServiceAccounts(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const queryClient = useQueryClient()

  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: workspaceServiceAccountsQueryKey(workspaceId),
    queryFn: async () =>
      await serviceAccountsListWorkspaceServiceAccounts({
        workspaceId,
        limit: 100,
      }),
    enabled: enabled && Boolean(workspaceId),
  })

  function invalidate() {
    return queryClient.invalidateQueries({
      queryKey: workspaceServiceAccountsQueryKey(workspaceId),
    })
  }

  function invalidateApiKeys(serviceAccountId: string) {
    return queryClient.invalidateQueries({
      queryKey: workspaceServiceAccountApiKeysQueryKey(
        workspaceId,
        serviceAccountId
      ),
    })
  }

  const { mutateAsync: createServiceAccount, isPending: createPending } =
    useMutation({
      mutationFn: async (
        requestBody: ServiceAccountCreate
      ): Promise<ServiceAccountApiKeyIssueResponse> =>
        await serviceAccountsCreateWorkspaceServiceAccount({
          workspaceId,
          requestBody,
        }),
      onSuccess: invalidate,
    })

  const { mutateAsync: updateServiceAccount, isPending: updatePending } =
    useMutation({
      mutationFn: async (params: {
        serviceAccountId: string
        requestBody: ServiceAccountUpdate
      }) =>
        await serviceAccountsUpdateWorkspaceServiceAccount({
          workspaceId,
          serviceAccountId: params.serviceAccountId,
          requestBody: params.requestBody,
        }),
      onSuccess: invalidate,
    })

  const { mutateAsync: disableServiceAccount, isPending: disablePending } =
    useMutation({
      mutationFn: async (serviceAccountId: string) =>
        await serviceAccountsDisableWorkspaceServiceAccount({
          workspaceId,
          serviceAccountId,
        }),
      onSuccess: invalidate,
    })

  const { mutateAsync: enableServiceAccount, isPending: enablePending } =
    useMutation({
      mutationFn: async (serviceAccountId: string) =>
        await serviceAccountsEnableWorkspaceServiceAccount({
          workspaceId,
          serviceAccountId,
        }),
      onSuccess: invalidate,
    })

  const { mutateAsync: issueApiKey, isPending: issueApiKeyPending } =
    useMutation({
      mutationFn: async (params: {
        serviceAccountId: string
        requestBody: ServiceAccountApiKeyCreate
      }) =>
        await serviceAccountsCreateWorkspaceServiceAccountApiKey({
          workspaceId,
          serviceAccountId: params.serviceAccountId,
          requestBody: params.requestBody,
        }),
      onSuccess: async (_result, variables) => {
        await invalidate()
        await invalidateApiKeys(variables.serviceAccountId)
      },
    })

  const { mutateAsync: revokeApiKey, isPending: revokeApiKeyPending } =
    useMutation({
      mutationFn: async (params: {
        serviceAccountId: string
        apiKeyId: string
      }) =>
        await serviceAccountsRevokeWorkspaceServiceAccountApiKey({
          workspaceId,
          serviceAccountId: params.serviceAccountId,
          apiKeyId: params.apiKeyId,
        }),
      onSuccess: async (_result, variables) => {
        await invalidate()
        await invalidateApiKeys(variables.serviceAccountId)
      },
    })

  return {
    serviceAccounts: response?.items ?? [],
    nextCursor: response?.next_cursor ?? null,
    isLoading,
    error,
    createServiceAccount,
    createPending,
    updateServiceAccount,
    updatePending,
    disableServiceAccount,
    disablePending,
    enableServiceAccount,
    enablePending,
    issueApiKey,
    issueApiKeyPending,
    revokeApiKey,
    revokeApiKeyPending,
  }
}

export function useWorkspaceServiceAccountApiKeys(
  workspaceId: string,
  serviceAccountId: string | null
) {
  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey:
      workspaceId && serviceAccountId
        ? workspaceServiceAccountApiKeysQueryKey(workspaceId, serviceAccountId)
        : [
            "workspace-service-accounts",
            workspaceId,
            "no-service-account",
            "api-keys",
          ],
    queryFn: async () =>
      await serviceAccountsListWorkspaceServiceAccountApiKeys({
        workspaceId,
        serviceAccountId: serviceAccountId!,
        limit: 100,
      }),
    enabled: Boolean(workspaceId && serviceAccountId),
  })

  return {
    apiKeys: (response?.items ?? []) as ServiceAccountApiKeyRead[],
    nextCursor: response?.next_cursor ?? null,
    isLoading,
    error,
  }
}

export function useWorkspaceServiceAccountScopes(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["workspace-service-account-scopes", workspaceId],
    queryFn: async () =>
      await serviceAccountsListWorkspaceServiceAccountScopes({ workspaceId }),
    enabled: enabled && Boolean(workspaceId),
  })

  return {
    scopes: (response?.items ?? []) as ServiceAccountScopeRead[],
    isLoading,
    error,
  }
}
