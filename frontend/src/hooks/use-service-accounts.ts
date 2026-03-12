"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ServiceAccountApiKeyCreate,
  type ServiceAccountCreate,
  type ServiceAccountScopeRead,
  type ServiceAccountUpdate,
  serviceAccountsCreateOrganizationServiceAccount,
  serviceAccountsCreateWorkspaceServiceAccount,
  serviceAccountsDisableOrganizationServiceAccount,
  serviceAccountsDisableWorkspaceServiceAccount,
  serviceAccountsEnableOrganizationServiceAccount,
  serviceAccountsEnableWorkspaceServiceAccount,
  serviceAccountsListOrganizationServiceAccountScopes,
  serviceAccountsListOrganizationServiceAccounts,
  serviceAccountsListWorkspaceServiceAccountScopes,
  serviceAccountsListWorkspaceServiceAccounts,
  serviceAccountsRegenerateOrganizationServiceAccountKey,
  serviceAccountsRegenerateWorkspaceServiceAccountKey,
  serviceAccountsRevokeOrganizationServiceAccountKey,
  serviceAccountsRevokeWorkspaceServiceAccountKey,
  serviceAccountsUpdateOrganizationServiceAccount,
  serviceAccountsUpdateWorkspaceServiceAccount,
} from "@/client"

export function useOrganizationServiceAccounts() {
  const queryClient = useQueryClient()

  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["organization-service-accounts"],
    queryFn: async () =>
      await serviceAccountsListOrganizationServiceAccounts({ limit: 100 }),
  })

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["organization-service-accounts"],
    })

  const { mutateAsync: createServiceAccount, isPending: createPending } =
    useMutation({
      mutationFn: async (requestBody: ServiceAccountCreate) =>
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

  const { mutateAsync: regenerateKey, isPending: regeneratePending } =
    useMutation({
      mutationFn: async (params: {
        serviceAccountId: string
        requestBody: ServiceAccountApiKeyCreate
      }) =>
        await serviceAccountsRegenerateOrganizationServiceAccountKey({
          serviceAccountId: params.serviceAccountId,
          requestBody: params.requestBody,
        }),
      onSuccess: invalidate,
    })

  const { mutateAsync: revokeKey, isPending: revokePending } = useMutation({
    mutationFn: async (serviceAccountId: string) =>
      await serviceAccountsRevokeOrganizationServiceAccountKey({
        serviceAccountId,
      }),
    onSuccess: invalidate,
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
    regenerateKey,
    regeneratePending,
    revokeKey,
    revokePending,
  }
}

export function useOrganizationServiceAccountScopes() {
  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["organization-service-account-scopes"],
    queryFn: async () =>
      await serviceAccountsListOrganizationServiceAccountScopes(),
  })

  return {
    scopes: (response?.items ?? []) as ServiceAccountScopeRead[],
    isLoading,
    error,
  }
}

export function useWorkspaceServiceAccounts(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["workspace-service-accounts", workspaceId],
    queryFn: async () =>
      await serviceAccountsListWorkspaceServiceAccounts({
        workspaceId,
        limit: 100,
      }),
    enabled: Boolean(workspaceId),
  })

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["workspace-service-accounts", workspaceId],
    })

  const { mutateAsync: createServiceAccount, isPending: createPending } =
    useMutation({
      mutationFn: async (requestBody: ServiceAccountCreate) =>
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

  const { mutateAsync: regenerateKey, isPending: regeneratePending } =
    useMutation({
      mutationFn: async (params: {
        serviceAccountId: string
        requestBody: ServiceAccountApiKeyCreate
      }) =>
        await serviceAccountsRegenerateWorkspaceServiceAccountKey({
          workspaceId,
          serviceAccountId: params.serviceAccountId,
          requestBody: params.requestBody,
        }),
      onSuccess: invalidate,
    })

  const { mutateAsync: revokeKey, isPending: revokePending } = useMutation({
    mutationFn: async (serviceAccountId: string) =>
      await serviceAccountsRevokeWorkspaceServiceAccountKey({
        workspaceId,
        serviceAccountId,
      }),
    onSuccess: invalidate,
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
    regenerateKey,
    regeneratePending,
    revokeKey,
    revokePending,
  }
}

export function useWorkspaceServiceAccountScopes(workspaceId: string) {
  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["workspace-service-account-scopes", workspaceId],
    queryFn: async () =>
      await serviceAccountsListWorkspaceServiceAccountScopes({ workspaceId }),
    enabled: Boolean(workspaceId),
  })

  return {
    scopes: (response?.items ?? []) as ServiceAccountScopeRead[],
    isLoading,
    error,
  }
}
