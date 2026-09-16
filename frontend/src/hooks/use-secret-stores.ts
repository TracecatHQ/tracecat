"use client"

import {
  type ApiError,
  type AwsSecretReferenceCreate,
  type AwsSecretReferenceUpdate,
  organizationSecretStoresAuthorizeSecretStoreWorkspace,
  organizationSecretStoresCreateSecretStore,
  organizationSecretStoresDeleteSecretStore,
  organizationSecretStoresListSecretStores,
  organizationSecretStoresRevokeSecretStoreWorkspace,
  organizationSecretStoresUpdateSecretStore,
  type SecretReferenceCheckResult,
  type SecretStoreCreate,
  type SecretStoreRead,
  type SecretStoreUpdate,
  secretsCheckAwsSecretReference,
  secretsCreateAwsSecretReference,
  secretsListAuthorizedSecretStores,
  secretsUpdateAwsSecretReference,
  type WorkspaceSecretStoreRead,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

const ORG_SECRET_STORES_KEY = ["organization-secret-stores"]

/**
 * Organization-level management of external secret stores (AWS Secrets
 * Manager). Tracecat stores role/region metadata and a persisted external ID;
 * it never stores AWS credentials or remote values.
 */
export function useOrgSecretStores() {
  const queryClient = useQueryClient()
  const {
    data: stores,
    isLoading,
    error,
  } = useQuery<SecretStoreRead[], ApiError>({
    queryKey: ORG_SECRET_STORES_KEY,
    queryFn: organizationSecretStoresListSecretStores,
    retry: false,
  })

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ORG_SECRET_STORES_KEY })
  }

  const { mutateAsync: createStore, isPending: createStorePending } =
    useMutation({
      mutationFn: async (params: SecretStoreCreate) =>
        await organizationSecretStoresCreateSecretStore({
          requestBody: params,
        }),
      onSuccess: () => {
        toast({ title: "Secret store created" })
        invalidate()
      },
      onError: (err: ApiError) => {
        toast({
          title: "Failed to create secret store",
          description: describeApiError(err),
        })
      },
    })

  const { mutateAsync: updateStore } = useMutation({
    mutationFn: async ({
      storeId,
      params,
    }: {
      storeId: string
      params: SecretStoreUpdate
    }) =>
      await organizationSecretStoresUpdateSecretStore({
        storeId,
        requestBody: params,
      }),
    onSuccess: () => {
      toast({ title: "Secret store updated" })
      invalidate()
    },
    onError: (err: ApiError) => {
      toast({
        title: "Failed to update secret store",
        description: describeApiError(err),
      })
    },
  })

  const { mutateAsync: deleteStore } = useMutation({
    mutationFn: async (storeId: string) =>
      await organizationSecretStoresDeleteSecretStore({ storeId }),
    onSuccess: () => {
      toast({ title: "Secret store deleted" })
      invalidate()
    },
    onError: (err: ApiError) => {
      toast({
        title: "Failed to delete secret store",
        description: describeApiError(err),
      })
    },
  })

  const { mutateAsync: authorizeWorkspace } = useMutation({
    mutationFn: async ({
      storeId,
      workspaceId,
    }: {
      storeId: string
      workspaceId: string
    }) =>
      await organizationSecretStoresAuthorizeSecretStoreWorkspace({
        storeId,
        requestBody: { workspace_id: workspaceId },
      }),
    onSuccess: () => {
      toast({ title: "Workspace authorized" })
      invalidate()
    },
    onError: (err: ApiError) => {
      toast({
        title: "Failed to authorize workspace",
        description: describeApiError(err),
      })
    },
  })

  const { mutateAsync: revokeWorkspace } = useMutation({
    mutationFn: async ({
      storeId,
      workspaceId,
    }: {
      storeId: string
      workspaceId: string
    }) =>
      await organizationSecretStoresRevokeSecretStoreWorkspace({
        storeId,
        workspaceId,
      }),
    onSuccess: () => {
      toast({ title: "Workspace authorization revoked" })
      invalidate()
    },
    onError: (err: ApiError) => {
      toast({
        title: "Failed to revoke workspace",
        description: describeApiError(err),
      })
    },
  })

  return {
    stores,
    isLoading,
    error,
    createStore,
    createStorePending,
    updateStore,
    deleteStore,
    authorizeWorkspace,
    revokeWorkspace,
  }
}

/**
 * Stores the current workspace has been authorized to reference.
 */
export function useAuthorizedSecretStores(
  workspaceId: string,
  options: { enabled?: boolean } = {}
) {
  const {
    data: stores,
    isLoading,
    error,
  } = useQuery<WorkspaceSecretStoreRead[], ApiError>({
    queryKey: ["workspace-secret-stores", workspaceId],
    queryFn: async () =>
      await secretsListAuthorizedSecretStores({ workspaceId }),
    enabled: !!workspaceId && (options.enabled ?? true),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  return { stores, isLoading, error }
}

/**
 * Create, update, and check AWS-backed workspace secret references.
 * These calls send references and key mappings only, never values.
 */
export function useAwsSecretReferences(workspaceId: string) {
  const queryClient = useQueryClient()

  function invalidateSecrets() {
    queryClient.invalidateQueries({
      queryKey: ["workspace-secrets", workspaceId],
    })
  }

  const { mutateAsync: createReference } = useMutation({
    mutationFn: async (params: AwsSecretReferenceCreate) =>
      await secretsCreateAwsSecretReference({
        workspaceId,
        requestBody: params,
      }),
    onSuccess: () => {
      toast({
        title: "Added AWS-backed secret",
        description: "The reference was saved. Values stay in AWS.",
      })
      invalidateSecrets()
    },
    onError: (err: ApiError) => {
      toast({
        title: "Failed to add AWS-backed secret",
        description: describeApiError(err),
      })
    },
  })

  const { mutateAsync: updateReference } = useMutation({
    mutationFn: async ({
      secretId,
      params,
    }: {
      secretId: string
      params: AwsSecretReferenceUpdate
    }) =>
      await secretsUpdateAwsSecretReference({
        workspaceId,
        secretId,
        requestBody: params,
      }),
    onSuccess: () => {
      toast({ title: "Updated AWS-backed secret" })
      invalidateSecrets()
    },
    onError: (err: ApiError) => {
      toast({
        title: "Failed to update AWS-backed secret",
        description: describeApiError(err),
      })
    },
  })

  const { mutateAsync: checkReference, isPending: checkReferencePending } =
    useMutation<SecretReferenceCheckResult, ApiError, string>({
      mutationFn: async (secretId: string) =>
        await secretsCheckAwsSecretReference({ workspaceId, secretId }),
    })

  return {
    createReference,
    updateReference,
    checkReference,
    checkReferencePending,
  }
}

function describeApiError(err: ApiError): string {
  const body = err.body
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === "string") {
      return detail
    }
  }
  return err.message
}
