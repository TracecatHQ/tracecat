"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  CatalogAuthOption,
  CatalogConnectionRead,
  CatalogIntegrationDetail,
  CatalogIntegrationRead,
  CatalogStaticKVConnectionCreate,
  IntegrationRead,
  IntegrationSource,
  IntegrationTestConnectionResponse,
  IntegrationUpdate,
  OAuthGrantType,
  ProviderRead,
} from "@/client"
import {
  integrationsCatalogCreateConnection,
  integrationsCatalogDeleteConnection,
  integrationsCatalogGetCatalogEntry,
  integrationsCatalogListCatalog,
  integrationsCatalogListConnections,
  integrationsDeleteIntegration,
  integrationsGetIntegration,
  integrationsTestConnection,
  integrationsUpdateIntegration,
  providersGetProvider,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { retryHandler, type TracecatApiError } from "@/lib/errors"

const CATALOG_LIST_KEY = "integrations-catalog"
const CATALOG_DETAIL_KEY = "integrations-catalog-detail"
const CATALOG_CONNECTIONS_KEY = "integrations-catalog-connections"

export type CatalogConnectionCreatePayload = CatalogStaticKVConnectionCreate & {
  auth_method: "static_kv"
}

export interface UseListIntegrationCatalogOptions {
  source?: IntegrationSource | null
  search?: string | null
}

export function useListIntegrationCatalog(
  workspaceId: string,
  options: UseListIntegrationCatalogOptions = {}
) {
  const { source = null, search = null } = options
  const {
    data: catalog,
    isLoading: catalogIsLoading,
    error: catalogError,
  } = useQuery<CatalogIntegrationRead[], TracecatApiError>({
    queryKey: [CATALOG_LIST_KEY, workspaceId, source, search],
    queryFn: async () =>
      await integrationsCatalogListCatalog({
        workspaceId,
        source,
        search,
      }),
    enabled: Boolean(workspaceId),
    staleTime: 30 * 1000,
    refetchOnWindowFocus: false,
  })

  return { catalog, catalogIsLoading, catalogError }
}

export function useIntegrationDetail(
  workspaceId: string,
  integrationId: string | null | undefined
) {
  const {
    data: integration,
    isLoading: integrationIsLoading,
    error: integrationError,
  } = useQuery<CatalogIntegrationDetail, TracecatApiError>({
    queryKey: [CATALOG_DETAIL_KEY, workspaceId, integrationId],
    queryFn: async () =>
      await integrationsCatalogGetCatalogEntry({
        workspaceId,
        integrationId: integrationId!,
      }),
    enabled: Boolean(workspaceId && integrationId),
    staleTime: 15 * 1000,
    refetchOnWindowFocus: false,
  })

  return { integration, integrationIsLoading, integrationError }
}

function invalidateCatalogQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  workspaceId: string,
  integrationId?: string | null
) {
  queryClient.invalidateQueries({ queryKey: [CATALOG_LIST_KEY, workspaceId] })
  if (integrationId) {
    queryClient.invalidateQueries({
      queryKey: [CATALOG_DETAIL_KEY, workspaceId, integrationId],
    })
  }
}

function invalidateProviderAuthQueries({
  queryClient,
  workspaceId,
  integrationId,
  providerId,
  grantType,
}: {
  queryClient: ReturnType<typeof useQueryClient>
  workspaceId: string
  integrationId?: string | null
  providerId: string
  grantType: OAuthGrantType
}) {
  invalidateCatalogQueries(queryClient, workspaceId, integrationId)
  queryClient.invalidateQueries({
    queryKey: ["integration", providerId, workspaceId, grantType],
  })
  queryClient.invalidateQueries({
    queryKey: ["provider-schema", providerId, workspaceId, grantType],
  })
  queryClient.invalidateQueries({
    queryKey: ["providers", workspaceId],
  })
}

export function useListConnections(
  workspaceId: string,
  integrationId: string | null | undefined
) {
  const {
    data: connections,
    isLoading: connectionsIsLoading,
    error: connectionsError,
  } = useQuery<CatalogConnectionRead[], TracecatApiError>({
    queryKey: [CATALOG_CONNECTIONS_KEY, workspaceId, integrationId],
    queryFn: async () =>
      await integrationsCatalogListConnections({
        workspaceId,
        integrationId: integrationId!,
      }),
    enabled: Boolean(workspaceId && integrationId),
    staleTime: 15 * 1000,
    refetchOnWindowFocus: false,
  })

  return { connections, connectionsIsLoading, connectionsError }
}

export function useCreateConnection(
  workspaceId: string,
  integrationId: string
) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: createConnection,
    isPending: createConnectionIsPending,
    error: createConnectionError,
  } = useMutation({
    mutationFn: async (params: CatalogConnectionCreatePayload) =>
      await integrationsCatalogCreateConnection({
        workspaceId,
        integrationId,
        requestBody: params,
      }),
    onSuccess: (connection) => {
      queryClient.invalidateQueries({
        queryKey: [CATALOG_CONNECTIONS_KEY, workspaceId, integrationId],
      })
      queryClient.invalidateQueries({
        queryKey: [CATALOG_DETAIL_KEY, workspaceId, integrationId],
      })
      queryClient.invalidateQueries({
        queryKey: [CATALOG_LIST_KEY, workspaceId],
      })
      toast({
        title: "Connection added",
        description: connection.label,
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to create connection:", error)
      toast({
        title: "Failed to add connection",
        description: `${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    createConnection,
    createConnectionIsPending,
    createConnectionError,
  }
}

export function useProviderAuthConfig(
  workspaceId: string,
  authOption: CatalogAuthOption | null | undefined,
  enabled: boolean
) {
  const providerId = authOption?.provider_id ?? null
  const grantType = authOption?.grant_type ?? null
  const hasConfiguredProvider = authOption?.status !== "not_configured"

  const {
    data: provider,
    isLoading: providerIsLoading,
    error: providerError,
  } = useQuery<ProviderRead, TracecatApiError>({
    queryKey: ["provider-schema", providerId, workspaceId, grantType],
    queryFn: async () =>
      await providersGetProvider({
        providerId: providerId!,
        workspaceId,
        grantType: grantType!,
      }),
    enabled: Boolean(enabled && workspaceId && providerId && grantType),
    retry: retryHandler,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const {
    data: integration,
    isLoading: integrationIsLoading,
    error: integrationError,
  } = useQuery<IntegrationRead | null, TracecatApiError>({
    queryKey: ["integration", providerId, workspaceId, grantType],
    queryFn: async () => {
      try {
        return await integrationsGetIntegration({
          providerId: providerId!,
          workspaceId,
          grantType: grantType!,
        })
      } catch (error) {
        if ((error as TracecatApiError).status === 404) {
          return null
        }
        throw error
      }
    },
    enabled: Boolean(
      enabled && hasConfiguredProvider && workspaceId && providerId && grantType
    ),
    retry: retryHandler,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  return {
    provider,
    providerIsLoading,
    providerError,
    integration,
    integrationIsLoading,
    integrationError,
  }
}

export function useUpdateProviderAuthConfig({
  workspaceId,
  integrationId,
  providerId,
  grantType,
}: {
  workspaceId: string
  integrationId: string
  providerId: string
  grantType: OAuthGrantType
}) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: updateProviderAuthConfig,
    isPending: updateProviderAuthConfigIsPending,
    error: updateProviderAuthConfigError,
  } = useMutation({
    mutationFn: async (params: Omit<IntegrationUpdate, "grant_type">) =>
      await integrationsUpdateIntegration({
        workspaceId,
        providerId,
        grantType,
        requestBody: {
          ...params,
          grant_type: grantType,
        },
      }),
    onSuccess: () => {
      invalidateProviderAuthQueries({
        queryClient,
        workspaceId,
        integrationId,
        providerId,
        grantType,
      })
      toast({ title: "Configuration saved" })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to save provider configuration:", error)
      toast({
        title: "Failed to save configuration",
        description: `${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    updateProviderAuthConfig,
    updateProviderAuthConfigIsPending,
    updateProviderAuthConfigError,
  }
}

export function useDeleteProviderAuthConfig({
  workspaceId,
  integrationId,
  providerId,
  grantType,
}: {
  workspaceId: string
  integrationId: string
  providerId: string
  grantType: OAuthGrantType
}) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: deleteProviderAuthConfig,
    isPending: deleteProviderAuthConfigIsPending,
    error: deleteProviderAuthConfigError,
  } = useMutation({
    mutationFn: async () =>
      await integrationsDeleteIntegration({
        workspaceId,
        providerId,
        grantType,
      }),
    onSuccess: () => {
      invalidateProviderAuthQueries({
        queryClient,
        workspaceId,
        integrationId,
        providerId,
        grantType,
      })
      toast({ title: "Configuration removed" })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to remove provider configuration:", error)
      toast({
        title: "Failed to remove configuration",
        description: `${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    deleteProviderAuthConfig,
    deleteProviderAuthConfigIsPending,
    deleteProviderAuthConfigError,
  }
}

export function useTestProviderAuthConnection({
  workspaceId,
  integrationId,
  providerId,
  grantType,
}: {
  workspaceId: string
  integrationId: string
  providerId: string
  grantType: OAuthGrantType
}) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: testProviderAuthConnection,
    isPending: testProviderAuthConnectionIsPending,
    error: testProviderAuthConnectionError,
  } = useMutation<IntegrationTestConnectionResponse, TracecatApiError>({
    mutationFn: async () =>
      await integrationsTestConnection({
        workspaceId,
        providerId,
      }),
    onSuccess: (result) => {
      invalidateProviderAuthQueries({
        queryClient,
        workspaceId,
        integrationId,
        providerId,
        grantType,
      })
      if (result.success) {
        toast({
          title: "Connection successful",
          description: result.message,
        })
      } else {
        toast({
          title: "Connection failed",
          description: result.error || result.message,
          variant: "destructive",
        })
      }
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to test provider connection:", error)
      toast({
        title: "Test failed",
        description: `${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    testProviderAuthConnection,
    testProviderAuthConnectionIsPending,
    testProviderAuthConnectionError,
  }
}

export function useDeleteConnection(
  workspaceId: string,
  integrationId: string | null
) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: deleteConnection,
    isPending: deleteConnectionIsPending,
    error: deleteConnectionError,
  } = useMutation({
    mutationFn: async (connectionId: string) =>
      await integrationsCatalogDeleteConnection({
        workspaceId,
        connectionId,
      }),
    onSuccess: () => {
      if (integrationId) {
        queryClient.invalidateQueries({
          queryKey: [CATALOG_CONNECTIONS_KEY, workspaceId, integrationId],
        })
        queryClient.invalidateQueries({
          queryKey: [CATALOG_DETAIL_KEY, workspaceId, integrationId],
        })
      } else {
        queryClient.invalidateQueries({
          queryKey: [CATALOG_CONNECTIONS_KEY, workspaceId],
        })
      }
      toast({ title: "Connection removed" })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to delete connection:", error)
      toast({
        title: "Failed to remove connection",
        description: `${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    deleteConnection,
    deleteConnectionIsPending,
    deleteConnectionError,
  }
}
