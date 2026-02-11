"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  KeyRound,
  Loader2,
  Lock,
  LockKeyhole,
  RotateCcw,
  Sparkles,
  SquareAsterisk,
  WrenchIcon,
} from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type {
  IntegrationStatus,
  OAuthGrantType,
  SecretDefinition,
} from "@/client"
import {
  integrationsConnectProvider,
  integrationsDisconnectIntegration,
  integrationsTestConnection,
} from "@/client"
import { ProviderIcon, SecretIcon } from "@/components/icons"
import {
  type ConnectionFilter,
  IntegrationsHeader,
  type IntegrationTypeFilter,
} from "@/components/integrations/integrations-header"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Input } from "@/components/ui/input"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { CreateCredentialDialog } from "@/components/workspaces/create-credential-dialog"
import type { TracecatApiError } from "@/lib/errors"
import {
  useDeleteMcpIntegration,
  useIntegrations,
  useListMcpIntegrations,
  useSecretDefinitions,
  useWorkspaceSecrets,
} from "@/lib/hooks"
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
  | {
      type: "credential"
      id: string
      name: string
      description: string | null
      definition: SecretDefinition
    }

const displayStatus = (status: IntegrationStatus) =>
  status === "configured" ? "not_configured" : status

const integrationTypeIcons = {
  credential: KeyRound,
  oauth: Lock,
  mcp: Sparkles,
  custom_oauth: LockKeyhole,
  custom_mcp: WrenchIcon,
} as const

const integrationTypeLabels = {
  credential: "Credential",
  oauth: "OAuth",
  mcp: "MCP",
  custom_oauth: "Custom OAuth",
  custom_mcp: "Custom MCP",
} as const

export default function IntegrationsPage() {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const searchParams = useSearchParams()
  const [searchQuery, setSearchQuery] = useState("")
  const [typeFilters, setTypeFilters] = useState<IntegrationTypeFilter[]>([])
  const [connectionFilter, setConnectionFilter] =
    useState<ConnectionFilter>("all")
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
  const [activeCredentialTemplate, setActiveCredentialTemplate] =
    useState<SecretDefinition | null>(null)
  const [pendingMcpDeleteId, setPendingMcpDeleteId] = useState<string | null>(
    null
  )
  const [disconnectConfirmTextByKey, setDisconnectConfirmTextByKey] = useState<
    Record<string, string>
  >({})
  const lastHandledConnectRef = useRef<string | null>(null)

  const { integrations, providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)
  const { mcpIntegrations, mcpIntegrationsIsLoading, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId)
  const {
    secretDefinitions,
    secretDefinitionsIsLoading,
    secretDefinitionsError,
  } = useSecretDefinitions(workspaceId)
  const { secrets, secretsIsLoading, secretsError } =
    useWorkspaceSecrets(workspaceId)
  const { deleteMcpIntegration, deleteMcpIntegrationIsPending } =
    useDeleteMcpIntegration(workspaceId)

  const queryClient = useQueryClient()
  const handleTypeFilterToggle = useCallback(
    (filter: IntegrationTypeFilter) => {
      setTypeFilters((prev) =>
        prev.includes(filter)
          ? prev.filter((value) => value !== filter)
          : [...prev, filter]
      )
    },
    []
  )

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
    }) => await integrationsTestConnection({ providerId, workspaceId }),
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
      new Map(
        (integrations ?? []).map((integration) => [integration.id, integration])
      ),
    [integrations]
  )

  const credentialEnvironments = useMemo(() => {
    const environmentMap = new Map<string, Set<string>>()
    for (const secret of secrets ?? []) {
      const environment = secret.environment?.trim() || "default"
      const current = environmentMap.get(secret.name) ?? new Set<string>()
      current.add(environment)
      environmentMap.set(secret.name, current)
    }

    return new Map(
      Array.from(environmentMap.entries(), ([name, environments]) => [
        name,
        Array.from(environments).sort((a, b) => a.localeCompare(b)),
      ])
    )
  }, [secrets])

  const getIntegrationStatus = useCallback(
    (item: IntegrationItem): IntegrationStatus => {
      if (item.type === "credential") {
        return credentialEnvironments.has(item.name)
          ? "connected"
          : "not_configured"
      }
      if (item.type === "mcp") {
        return getMcpDisplayStatus(item, integrationById)
      }
      return displayStatus(item.integration_status)
    },
    [credentialEnvironments, integrationById]
  )

  const isCustomMcpIntegration = useCallback(
    (item: Extract<IntegrationItem, { type: "mcp" }>) => {
      if (item.auth_type !== "OAUTH2") {
        return true
      }
      if (!item.oauth_integration_id) {
        return true
      }
      const integration = integrationById.get(item.oauth_integration_id)
      return !integration?.provider_id.endsWith("_mcp")
    },
    [integrationById]
  )

  const allIntegrations = useMemo<IntegrationItem[]>(() => {
    // Track which MCP provider IDs already have MCP integration records
    // so we don't show duplicates for connected MCP OAuth providers
    const mcpOAuthProviderIds = new Set(
      (mcpIntegrations ?? [])
        .filter((mcp) => mcp.auth_type === "OAUTH2" && mcp.oauth_integration_id)
        .flatMap((mcp) => {
          const integration = integrationById.get(mcp.oauth_integration_id!)
          return integration?.provider_id ? [integration.provider_id] : []
        })
    )

    const oauthItems: IntegrationItem[] =
      providers
        ?.filter((provider) => {
          // Exclude MCP OAuth providers that already have MCP integration records
          if (provider.id.endsWith("_mcp")) {
            return !mcpOAuthProviderIds.has(provider.id)
          }
          return true
        })
        .map((provider) => ({
          type: "oauth" as const,
          id: provider.id,
          name: provider.name,
          description: provider.description,
          enabled: provider.enabled,
          integration_status: provider.integration_status,
          grant_type: provider.grant_type,
          requires_config: provider.requires_config,
        })) ?? []

    const mcpItems: IntegrationItem[] =
      mcpIntegrations?.map((mcp) => ({
        type: "mcp" as const,
        id: mcp.id,
        name: mcp.name,
        description: mcp.description,
        slug: mcp.slug,
        server_uri: mcp.server_uri,
        auth_type: mcp.auth_type,
        oauth_integration_id: mcp.oauth_integration_id,
      })) ?? []

    const credentialItems: IntegrationItem[] =
      secretDefinitions?.map((secret) => ({
        type: "credential" as const,
        id: secret.name,
        name: secret.name,
        description: null,
        definition: secret,
      })) ?? []

    return [...oauthItems, ...mcpItems, ...credentialItems]
  }, [providers, mcpIntegrations, secretDefinitions, integrationById])

  const filteredIntegrations = useMemo(() => {
    const filtered = allIntegrations.filter((item) => {
      const matchesSearch =
        item.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (item.description ?? "")
          .toLowerCase()
          .includes(searchQuery.toLowerCase())
      const matchesType =
        typeFilters.length === 0 ||
        typeFilters.some((filter) => {
          if (filter === "custom_oauth") {
            return item.type === "oauth" && item.id.startsWith("custom_")
          }
          if (filter === "custom_mcp") {
            return item.type === "mcp" && isCustomMcpIntegration(item)
          }
          if (filter === "mcp") {
            return (
              item.type === "mcp" ||
              (item.type === "oauth" && item.id.endsWith("_mcp"))
            )
          }
          return item.type === filter
        })
      const status = getIntegrationStatus(item)
      const matchesConnection =
        connectionFilter === "all"
          ? true
          : connectionFilter === "connected"
            ? status === "connected"
            : status !== "connected"

      return matchesSearch && matchesType && matchesConnection
    })

    return [...filtered].sort((a, b) => {
      const aStatus = getIntegrationStatus(a)
      const bStatus = getIntegrationStatus(b)

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
  }, [
    allIntegrations,
    connectionFilter,
    getIntegrationStatus,
    isCustomMcpIntegration,
    searchQuery,
    typeFilters,
  ])

  const connectParam = searchParams?.get("connect")
  const connectGrantType = searchParams?.get(
    "grant_type"
  ) as OAuthGrantType | null

  useEffect(() => {
    if (!connectParam) {
      lastHandledConnectRef.current = null
    }
  }, [connectParam])

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

  const getDisconnectKey = useCallback((item: IntegrationItem) => {
    if (item.type === "oauth") {
      return `oauth:${item.id}:${item.grant_type}`
    }
    if (item.type === "mcp") {
      return `mcp:${item.id}`
    }
    return `credential:${item.id}`
  }, [])

  const resetDisconnectConfirmText = useCallback((key: string) => {
    setDisconnectConfirmTextByKey((prev) => {
      if (!prev[key]) {
        return prev
      }
      const next = { ...prev }
      delete next[key]
      return next
    })
  }, [])

  if (
    providersIsLoading ||
    mcpIntegrationsIsLoading ||
    secretDefinitionsIsLoading ||
    secretsIsLoading
  ) {
    return <CenteredSpinner />
  }
  if (
    providersError ||
    mcpIntegrationsError ||
    secretDefinitionsError ||
    secretsError
  ) {
    return (
      <div>
        Error:{" "}
        {providersError?.message ||
          mcpIntegrationsError?.message ||
          secretDefinitionsError?.message ||
          secretsError?.message}
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <IntegrationsHeader
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        typeFilters={typeFilters}
        onTypeFilterToggle={handleTypeFilterToggle}
        connectionFilter={connectionFilter}
        onConnectionFilterChange={setConnectionFilter}
      />

      {/* Integrations List */}
      <ScrollArea className="mt-6 flex-1 min-h-0 [&>[data-radix-scroll-area-viewport]]:[scrollbar-width:none] [&>[data-radix-scroll-area-viewport]::-webkit-scrollbar]:hidden [&>[data-orientation=vertical]]:!hidden [&>[data-orientation=horizontal]]:!hidden">
        <div className="mx-auto grid max-w-[1600px] grid-cols-1 gap-2 pb-10 pl-3 pr-4 md:grid-cols-2 xl:grid-cols-3">
          {filteredIntegrations.map((item) => {
            const isOAuth = item.type === "oauth"
            const isCredential = item.type === "credential"
            const isMcp = item.type === "mcp"
            const isCustomOAuth = isOAuth && item.id.startsWith("custom_")
            const isMcpOAuth = isOAuth && item.id.endsWith("_mcp")
            const isCustomMcp = isMcp && isCustomMcpIntegration(item)
            const status = getIntegrationStatus(item)
            const configuredEnvironments = isCredential
              ? (credentialEnvironments.get(item.name) ?? [])
              : []
            const isConnected = isMcp
              ? status === "connected"
              : isCredential
                ? false
                : item.integration_status === "connected"
            const isClickable =
              isCredential ||
              (isOAuth && isConnected) ||
              (isOAuth && item.requires_config && item.enabled) ||
              isMcp
            const isDisabled = isOAuth ? !item.enabled : false
            const showConnect = !isConnected
            const showDisconnect = isConnected && !isCredential
            const isConnecting =
              isOAuth &&
              ((connectProviderMutation.isPending &&
                connectProviderMutation.variables?.providerId === item.id) ||
                (testConnectionMutation.isPending &&
                  testConnectionMutation.variables?.providerId === item.id))
            const isDisconnecting =
              isOAuth &&
              disconnectProviderMutation.isPending &&
              disconnectProviderMutation.variables?.providerId === item.id
            const isDeletingMcp =
              item.type === "mcp" &&
              pendingMcpDeleteId === item.id &&
              deleteMcpIntegrationIsPending
            const disconnectKey = getDisconnectKey(item)
            const disconnectConfirmText =
              disconnectConfirmTextByKey[disconnectKey] ?? ""
            const displayType = isCustomOAuth
              ? "custom_oauth"
              : isMcpOAuth
                ? "mcp"
                : isCustomMcp
                  ? "custom_mcp"
                  : item.type
            const TypeIcon = integrationTypeIcons[displayType]
            const typeLabel = integrationTypeLabels[displayType]

            const mcpProviderIconId = isMcp
              ? item.slug.endsWith("_mcp")
                ? item.slug
                : `${item.slug}_mcp`
              : null

            return (
              <Item
                key={
                  isOAuth
                    ? `${item.id}-${item.grant_type}`
                    : item.type === "credential"
                      ? `credential-${item.id}`
                      : item.id
                }
                variant="outline"
                className={cn(
                  "flex-nowrap",
                  isClickable && "cursor-pointer hover:bg-muted/50",
                  !isClickable && "cursor-default"
                )}
                onClick={() => {
                  if (isCredential) {
                    setActiveCredentialTemplate(item.definition)
                    return
                  }
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
                    return
                  }
                  if (isMcp) {
                    setActiveMcpIntegrationId(item.id)
                  }
                }}
              >
                <ItemMedia>
                  {isOAuth ? (
                    <ProviderIcon
                      providerId={item.id}
                      className="size-7 rounded"
                    />
                  ) : isCredential ? (
                    <SecretIcon
                      secretName={item.name}
                      className="size-7 rounded"
                    />
                  ) : mcpProviderIconId ? (
                    <ProviderIcon
                      providerId={mcpProviderIconId}
                      className="size-7 rounded"
                    />
                  ) : (
                    <div className="flex size-7 items-center justify-center rounded bg-muted text-xs font-medium text-muted-foreground">
                      MCP
                    </div>
                  )}
                </ItemMedia>
                <ItemContent className="min-w-0">
                  <ItemTitle className="flex w-full min-w-0 items-center gap-2 text-[13px]">
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="min-w-0 truncate">{item.name}</span>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>{item.name}</p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span
                            role="img"
                            aria-label={`${typeLabel} integration`}
                            className="flex shrink-0 size-4 items-center justify-center rounded-sm border border-border bg-muted/70 text-muted-foreground"
                          >
                            <TypeIcon className="size-3" />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>{typeLabel}</p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </ItemTitle>
                </ItemContent>
                <ItemActions className="ml-auto flex shrink-0 items-center gap-1.5 pl-3">
                  {isOAuth && isConnected && (
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6"
                            aria-label={`Reconnect ${item.name}`}
                            onClick={(event) => {
                              event.stopPropagation()
                              handleReconnect(item.id, item.grant_type)
                            }}
                            disabled={isDisabled || isConnecting}
                          >
                            <RotateCcw className="size-3.5" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>Reconnect</p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  )}
                  {showConnect && (
                    <>
                      {isCredential && configuredEnvironments.length > 0 && (
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger asChild>
                            <button
                              type="button"
                              className="flex h-6 w-6 items-center justify-center"
                              aria-label={`View configured environments for ${item.name}`}
                              onClick={(event) => event.stopPropagation()}
                              onMouseDown={(event) => event.stopPropagation()}
                            >
                              <SquareAsterisk className="icon-success size-3.5" />
                            </button>
                          </HoverCardTrigger>
                          <HoverCardContent
                            className="w-auto max-w-[240px] p-3"
                            align="end"
                            side="top"
                            sideOffset={6}
                          >
                            <div className="space-y-2 text-xs">
                              <div className="font-medium text-foreground">
                                Configured environments
                              </div>
                              <div className="flex flex-wrap gap-1">
                                {configuredEnvironments.map((environment) => (
                                  <Badge
                                    key={environment}
                                    variant="secondary"
                                    className="text-[10px]"
                                  >
                                    {environment}
                                  </Badge>
                                ))}
                              </div>
                            </div>
                          </HoverCardContent>
                        </HoverCard>
                      )}
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-6 px-2.5 text-[11px] bg-white text-foreground hover:bg-muted border-input"
                        onClick={(event) => {
                          event.stopPropagation()
                          if (isCredential) {
                            setActiveCredentialTemplate(item.definition)
                            return
                          }
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
                          <Loader2 className="mr-1.5 size-3 animate-spin" />
                        ) : null}
                        {isCredential ? "Configure" : "Connect"}
                      </Button>
                    </>
                  )}
                  {showDisconnect && (
                    <AlertDialog
                      onOpenChange={(nextOpen) => {
                        if (!nextOpen) {
                          resetDisconnectConfirmText(disconnectKey)
                        }
                      }}
                    >
                      <AlertDialogTrigger asChild>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-6 px-2.5 text-[11px] bg-white text-foreground hover:bg-muted border-input"
                          onClick={(event) => event.stopPropagation()}
                          disabled={
                            isDisabled || isDisconnecting || isDeletingMcp
                          }
                        >
                          Disconnect
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent
                        onClick={(event) => event.stopPropagation()}
                      >
                        <AlertDialogHeader>
                          <AlertDialogTitle>
                            Disconnect integration
                          </AlertDialogTitle>
                          <AlertDialogDescription className="space-y-4">
                            <p>
                              {`Are you sure you want to disconnect from ${item.name}?`}
                            </p>
                            <div className="space-y-2">
                              <Label htmlFor={`${disconnectKey}-confirm`}>
                                Type <strong>{item.name}</strong> to confirm:
                              </Label>
                              <Input
                                id={`${disconnectKey}-confirm`}
                                value={disconnectConfirmText}
                                onChange={(event) =>
                                  setDisconnectConfirmTextByKey((prev) => ({
                                    ...prev,
                                    [disconnectKey]: event.target.value,
                                  }))
                                }
                                placeholder="Enter integration name"
                                disabled={isDisconnecting || isDeletingMcp}
                              />
                            </div>
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction
                            variant="destructive"
                            onClick={async () => {
                              if (disconnectConfirmText.trim() !== item.name) {
                                return
                              }
                              if (isOAuth) {
                                await handleOAuthDisconnect(
                                  item.id,
                                  item.grant_type
                                )
                              } else {
                                await handleMcpDisconnect(item.id)
                              }
                              resetDisconnectConfirmText(disconnectKey)
                            }}
                            disabled={
                              isDisconnecting ||
                              isDeletingMcp ||
                              disconnectConfirmText.trim() !== item.name
                            }
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
                </ItemActions>
              </Item>
            )
          })}
        </div>
        {filteredIntegrations.length === 0 && (
          <div className="py-12 text-center">
            <p className="text-sm text-muted-foreground">
              No integrations found matching your criteria.
            </p>
          </div>
        )}
      </ScrollArea>
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
      <CreateCredentialDialog
        open={Boolean(activeCredentialTemplate)}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            setActiveCredentialTemplate(null)
          }
        }}
        template={activeCredentialTemplate}
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
