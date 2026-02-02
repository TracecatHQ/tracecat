"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Loader2,
  RotateCcw,
  Search,
} from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { IntegrationStatus, OAuthGrantType } from "@/client"
import {
  integrationsConnectProvider,
  integrationsDisconnectIntegration,
  integrationsTestConnection,
} from "@/client"
import { ProviderIcon } from "@/components/icons"
import { MCPIntegrationDialog } from "@/components/integrations/mcp-integration-dialog"
import { OAuthIntegrationDetailsDialog } from "@/components/integrations/oauth-integration-details-dialog"
import { OAuthIntegrationDialog } from "@/components/integrations/oauth-integration-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  useDeleteMcpIntegration,
  useIntegrations,
  useListMcpIntegrations,
} from "@/lib/hooks"
import { type TracecatApiError } from "@/lib/errors"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

type IntegrationItem =
  | {
      type: "oauth"
      id: string
      name: string
      description: string
      enabled: boolean
      integration_status: IntegrationStatus
      grant_type: OAuthGrantType
      requires_config: boolean
    }
  | {
      type: "mcp"
      id: string
      name: string
      description: string | null
      slug: string
      server_uri: string
      auth_type: string
      oauth_integration_id?: string | null
    }

const displayStatus = (status: IntegrationStatus) =>
  status === "configured" ? "not_configured" : status

export default function IntegrationsPage() {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const searchParams = useSearchParams()
  const [searchQuery, setSearchQuery] = useState("")
  const [activeOAuthProvider, setActiveOAuthProvider] = useState<{
    providerId: string
    grantType: OAuthGrantType
  } | null>(null)
  const [detailsProvider, setDetailsProvider] = useState<{
    providerId: string
    grantType: OAuthGrantType
  } | null>(null)
  const [activeMcpIntegrationId, setActiveMcpIntegrationId] = useState<
    string | null
  >(null)
  const [pendingMcpDeleteId, setPendingMcpDeleteId] = useState<string | null>(
    null
  )
  const lastHandledConnectRef = useRef<string | null>(null)

  const { integrations, providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)
  const { mcpIntegrations, mcpIntegrationsIsLoading, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId)
  const { deleteMcpIntegration, deleteMcpIntegrationIsPending } =
    useDeleteMcpIntegration(workspaceId)

  const queryClient = useQueryClient()

  const invalidateIntegrationQueries = useCallback(
    (providerId: string, grantType: OAuthGrantType) => {
      queryClient.invalidateQueries({
        queryKey: ["integration", providerId, workspaceId, grantType],
      })
      queryClient.invalidateQueries({ queryKey: ["providers", workspaceId] })
      queryClient.invalidateQueries({
        queryKey: ["integrations", workspaceId],
      })
    },
    [queryClient, workspaceId]
  )

  const connectProviderMutation = useMutation({
    mutationFn: async ({ providerId }: { providerId: string }) =>
      await integrationsConnectProvider({ providerId, workspaceId }),
    onSuccess: (result) => {
      window.location.href = result.auth_url
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to connect provider:", error)
      toast({
        title: "Failed to connect",
        description: `Could not connect to provider: ${error.body?.detail || error.message}`,
      })
    },
  })

  const disconnectProviderMutation = useMutation({
    mutationFn: async ({
      providerId,
      grantType,
    }: {
      providerId: string
      grantType: OAuthGrantType
    }) =>
      await integrationsDisconnectIntegration({
        providerId,
        workspaceId,
        grantType,
      }),
    onSuccess: (_, variables) => {
      invalidateIntegrationQueries(variables.providerId, variables.grantType)
      toast({
        title: "Disconnected",
        description: "Successfully disconnected from provider",
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to disconnect provider:", error)
      toast({
        title: "Failed to disconnect",
        description: `Could not disconnect from provider: ${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  const testConnectionMutation = useMutation({
    mutationFn: async ({
      providerId,
    }: {
      providerId: string
      grantType: OAuthGrantType
    }) =>
      await integrationsTestConnection({ providerId, workspaceId }),
    onSuccess: (result, variables) => {
      if (result.success) {
        invalidateIntegrationQueries(variables.providerId, variables.grantType)
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
      console.error("Failed to test connection:", error)
      toast({
        title: "Test failed",
        description: `Could not test connection: ${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  const integrationById = useMemo(
    () =>
      new Map((integrations ?? []).map((integration) => [integration.id, integration])),
    [integrations]
  )

  const allIntegrations = useMemo<IntegrationItem[]>(() => {
    const oauthItems: IntegrationItem[] =
      providers?.map((provider) => ({
        type: "oauth" as const,
        id: provider.id,
        name: provider.name,
        description: provider.description,
        enabled: provider.enabled,
        integration_status: provider.integration_status,
        grant_type: provider.grant_type,
        requires_config: provider.requires_config,
      })) ?? []

    // Get OAuth integration IDs from providers that are _mcp providers
    const mcpProviderOAuthIds = new Set(
      providers
        ?.filter((provider) => provider.id.endsWith("_mcp"))
        .map((provider) => {
          // Find the integration for this provider
          const integration = integrations?.find(
            (int: { provider_id: string; id: string }) =>
              int.provider_id === provider.id
          )
          return integration?.id
        })
        .filter(Boolean) ?? []
    )

    // Filter out MCP integrations that correspond to built-in _mcp providers
    const mcpItems: IntegrationItem[] =
      mcpIntegrations
        ?.filter((mcp) => {
          // Filter out MCP integrations that reference built-in _mcp OAuth integrations
          if (
            mcp.oauth_integration_id &&
            mcpProviderOAuthIds.has(mcp.oauth_integration_id)
          ) {
            return false
          }
          return true
        })
        .map((mcp) => ({
          type: "mcp" as const,
          id: mcp.id,
          name: mcp.name,
          description: mcp.description,
          slug: mcp.slug,
          server_uri: mcp.server_uri,
          auth_type: mcp.auth_type,
          oauth_integration_id: mcp.oauth_integration_id,
        })) ?? []

    return [...oauthItems, ...mcpItems]
  }, [providers, mcpIntegrations, integrations])

  const filteredIntegrations = useMemo(() => {
    const filtered = allIntegrations.filter((item) => {
      const matchesSearch =
        item.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (item.description ?? "")
          .toLowerCase()
          .includes(searchQuery.toLowerCase())

      return matchesSearch
    })

    return [...filtered].sort((a, b) => {
      const aStatus: IntegrationStatus =
        a.type === "mcp"
          ? getMcpDisplayStatus(a, integrationById)
          : displayStatus(a.integration_status)
      const bStatus: IntegrationStatus =
        b.type === "mcp"
          ? getMcpDisplayStatus(b, integrationById)
          : displayStatus(b.integration_status)

      const statusOrder: Record<IntegrationStatus, number> = {
        connected: 0,
        not_configured: 1,
        configured: 1,
      }

      const aOrder = statusOrder[aStatus] ?? 1
      const bOrder = statusOrder[bStatus] ?? 1

      if (aOrder !== bOrder) {
        return aOrder - bOrder
      }

      if (a.type === "oauth" && b.type === "oauth") {
        if (a.enabled !== b.enabled) {
          return a.enabled ? -1 : 1
        }
      }

      return a.name.localeCompare(b.name)
    })
  }, [allIntegrations, integrationById, searchQuery])

  const connectParam = searchParams?.get("connect")
  const connectGrantType = searchParams?.get("grant_type") as
    | OAuthGrantType
    | null

  const clearConnectParams = useCallback(() => {
    const params = new URLSearchParams(searchParams?.toString() || "")
    params.delete("connect")
    params.delete("grant_type")
    const query = params.toString()
    router.replace(
      `/workspaces/${workspaceId}/integrations${query ? `?${query}` : ""}`
    )
  }, [router, searchParams, workspaceId])

  const setConnectParams = useCallback(
    (providerId: string, grantType: OAuthGrantType) => {
      const params = new URLSearchParams(searchParams?.toString() || "")
      params.set("connect", providerId)
      params.set("grant_type", grantType)
      router.replace(
        `/workspaces/${workspaceId}/integrations?${params.toString()}`
      )
    },
    [router, searchParams, workspaceId]
  )

  const handleOpenOAuthModal = useCallback(
    (providerId: string, grantType: OAuthGrantType) => {
      setActiveOAuthProvider({ providerId, grantType })
      setConnectParams(providerId, grantType)
    },
    [setConnectParams]
  )

  const handleOAuthDialogOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) {
        setActiveOAuthProvider(null)
        clearConnectParams()
      }
    },
    [clearConnectParams]
  )

  const handleDirectConnect = useCallback(
    (providerId: string, grantType: OAuthGrantType) => {
      if (grantType === "authorization_code") {
        connectProviderMutation.mutate({ providerId })
      } else {
        testConnectionMutation.mutate({ providerId, grantType })
      }
    },
    [connectProviderMutation, testConnectionMutation]
  )

  useEffect(() => {
    if (!connectParam || !providers) {
      return
    }

    const handleKey = `${connectParam}:${connectGrantType ?? ""}`
    if (lastHandledConnectRef.current === handleKey) {
      return
    }

    const provider = providers.find((item) => item.id === connectParam)
    if (!provider) {
      lastHandledConnectRef.current = handleKey
      return
    }

    lastHandledConnectRef.current = handleKey

    if (provider.requires_config) {
      handleOpenOAuthModal(provider.id, provider.grant_type)
      return
    }

    handleDirectConnect(provider.id, provider.grant_type)
    clearConnectParams()
  }, [
    clearConnectParams,
    connectGrantType,
    connectParam,
    handleDirectConnect,
    handleOpenOAuthModal,
    providers,
  ])

  const handleOAuthDisconnect = useCallback(
    async (providerId: string, grantType: OAuthGrantType) => {
      await disconnectProviderMutation.mutateAsync({ providerId, grantType })
    },
    [disconnectProviderMutation]
  )

  const handleReconnect = useCallback(
    (providerId: string, grantType: OAuthGrantType) => {
      handleDirectConnect(providerId, grantType)
    },
    [handleDirectConnect]
  )

  const handleMcpDisconnect = useCallback(
    async (mcpIntegrationId: string) => {
      setPendingMcpDeleteId(mcpIntegrationId)
      try {
        await deleteMcpIntegration(mcpIntegrationId)
      } finally {
        setPendingMcpDeleteId(null)
      }
    },
    [deleteMcpIntegration]
  )

  if (providersIsLoading || mcpIntegrationsIsLoading) {
    return <CenteredSpinner />
  }
  if (providersError || mcpIntegrationsError) {
    return (
      <div>
        Error: {providersError?.message || mcpIntegrationsError?.message}
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-0 max-w-5xl mx-auto my-16 px-8">
      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <Input
            placeholder="Search integrations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-9 border-gray-300 bg-gray-50 pl-10 text-sm focus:border-gray-400 focus:bg-white"
          />
        </div>
      </div>

      {/* Integrations List */}
      <div className="grid grid-cols-2 gap-2">
        {filteredIntegrations.map((item) => {
          const isOAuth = item.type === "oauth"
          const status: IntegrationStatus =
            item.type === "mcp"
              ? getMcpDisplayStatus(item, integrationById)
              : displayStatus(item.integration_status)
          const isConnected =
            item.type === "mcp"
              ? status === "connected"
              : item.integration_status === "connected"
          const isClickable =
            (isOAuth && isConnected) ||
            (isOAuth && item.requires_config && item.enabled) ||
            (!isOAuth && item.type === "mcp")
          const isDisabled = isOAuth ? !item.enabled : false
          const showConnect = !isConnected
          const showDisconnect = isConnected
          const isConnecting =
            (connectProviderMutation.isPending &&
              connectProviderMutation.variables?.providerId === item.id) ||
            (testConnectionMutation.isPending &&
              testConnectionMutation.variables?.providerId === item.id)
          const isDisconnecting =
            isOAuth &&
            disconnectProviderMutation.isPending &&
            disconnectProviderMutation.variables?.providerId === item.id
          const isDeletingMcp =
            !isOAuth &&
            pendingMcpDeleteId === item.id &&
            deleteMcpIntegrationIsPending

          return (
            <Item
              key={isOAuth ? `${item.id}-${item.grant_type}` : item.id}
              variant="outline"
              className={cn(
                isClickable && "cursor-pointer hover:bg-muted/50",
                !isClickable && "cursor-default"
              )}
              onClick={() => {
                if (isOAuth) {
                  if (isConnected) {
                    setDetailsProvider({
                      providerId: item.id,
                      grantType: item.grant_type,
                    })
                    return
                  }
                  if (item.requires_config && item.enabled) {
                    handleOpenOAuthModal(item.id, item.grant_type)
                  }
                } else if (item.type === "mcp") {
                  setActiveMcpIntegrationId(item.id)
                }
              }}
            >
              <ItemMedia>
                {isOAuth ? (
                  <ProviderIcon providerId={item.id} className="size-7 rounded" />
                ) : (
                  <div className="flex size-7 items-center justify-center rounded bg-muted text-xs font-medium text-muted-foreground">
                    MCP
                  </div>
                )}
              </ItemMedia>
              <ItemContent>
                <ItemTitle className="text-sm">{item.name}</ItemTitle>
              </ItemContent>
              <ItemActions className="ml-auto">
                {showConnect && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 px-3 text-xs bg-success text-success-foreground hover:bg-success/90 hover:text-success-foreground border-success"
                    onClick={(event) => {
                      event.stopPropagation()
                      if (isOAuth) {
                        if (item.requires_config) {
                          handleOpenOAuthModal(item.id, item.grant_type)
                          return
                        }
                        handleDirectConnect(item.id, item.grant_type)
                        return
                      }
                      setActiveMcpIntegrationId(item.id)
                    }}
                    disabled={isDisabled || isConnecting}
                  >
                    {isConnecting ? (
                      <Loader2 className="mr-2 size-3 animate-spin" />
                    ) : null}
                    Connect
                  </Button>
                )}
                {showDisconnect && (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 px-3 text-xs bg-destructive text-destructive-foreground hover:bg-destructive/90 hover:text-destructive-foreground border-destructive"
                        onClick={(event) => event.stopPropagation()}
                        disabled={isDisabled || isDisconnecting || isDeletingMcp}
                      >
                        Disconnect
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent onClick={(event) => event.stopPropagation()}>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Disconnect integration</AlertDialogTitle>
                        <AlertDialogDescription>
                          {isOAuth
                            ? `Are you sure you want to disconnect from ${item.name}?`
                            : `Disconnecting removes ${item.name} from this workspace. Continue?`}
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                          variant="destructive"
                          onClick={async () => {
                            if (isOAuth) {
                              await handleOAuthDisconnect(
                                item.id,
                                item.grant_type
                              )
                              return
                            }
                            await handleMcpDisconnect(item.id)
                          }}
                          disabled={isDisconnecting || isDeletingMcp}
                        >
                          {(isDisconnecting || isDeletingMcp) && (
                            <Loader2 className="mr-2 size-3 animate-spin" />
                          )}
                          Disconnect
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
                {isOAuth && isConnected && (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          aria-label={`Reconnect ${item.name}`}
                          onClick={(event) => {
                            event.stopPropagation()
                            handleReconnect(item.id, item.grant_type)
                          }}
                          disabled={isDisabled || isConnecting}
                        >
                          <RotateCcw className="size-4" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>Reconnect</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                )}
              </ItemActions>
            </Item>
          )
        })}
      </div>
      {filteredIntegrations.length === 0 && (
        <div className="col-span-2 py-12 text-center">
          <p className="text-sm text-muted-foreground">
            No integrations found matching your criteria.
          </p>
        </div>
      )}
      {activeOAuthProvider && (
        <OAuthIntegrationDialog
          open={Boolean(activeOAuthProvider)}
          onOpenChange={handleOAuthDialogOpenChange}
          providerId={activeOAuthProvider.providerId}
          grantType={activeOAuthProvider.grantType}
        />
      )}
      {detailsProvider && (
        <OAuthIntegrationDetailsDialog
          open={Boolean(detailsProvider)}
          onOpenChange={(nextOpen) => {
            if (!nextOpen) {
              setDetailsProvider(null)
            }
          }}
          providerId={detailsProvider.providerId}
          grantType={detailsProvider.grantType}
        />
      )}
      <MCPIntegrationDialog
        open={Boolean(activeMcpIntegrationId)}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            setActiveMcpIntegrationId(null)
          }
        }}
        mcpIntegrationId={activeMcpIntegrationId}
        hideTrigger
      />
    </div>
  )
}

function getMcpDisplayStatus(
  item: Extract<IntegrationItem, { type: "mcp" }>,
  integrationById: Map<string, { status: IntegrationStatus }>
): IntegrationStatus {
  if (item.auth_type === "OAUTH2") {
    if (!item.oauth_integration_id) {
      return "not_configured"
    }
    const integration = integrationById.get(item.oauth_integration_id)
    return integration?.status === "connected" ? "connected" : "not_configured"
  }
  return "connected"
}
