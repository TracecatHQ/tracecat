"use client"

import { ChevronRight, Loader2, RotateCcw, SquareAsterisk } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { IntegrationStatus, OAuthGrantType } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"
import { ProviderIcon } from "@/components/icons"
import {
  type ConnectionFilter,
  IntegrationsHeader,
  type IntegrationTypeFilter,
} from "@/components/integrations/integrations-header"
import { OAuthIntegrationDetailsDialog } from "@/components/integrations/oauth-integration-details-dialog"
import { OAuthIntegrationDialog } from "@/components/integrations/oauth-integration-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  useConnectProvider,
  useDisconnectProvider,
  useTestProvider,
} from "@/hooks/use-integration-actions"
import { useIntegrations } from "@/lib/hooks"
import { isMcpProvider } from "@/lib/integrations"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

type IntegrationItem = {
  type: "oauth"
  id: string
  name: string
  description: string
  enabled: boolean
  integration_status: IntegrationStatus
  grant_type: OAuthGrantType
  requires_config: boolean
}

const displayStatus = (status: IntegrationStatus) =>
  status === "configured" ? "not_configured" : status

type IntegrationSectionType = "oauth" | "custom_oauth"

const integrationTypeLabels = {
  oauth: "OAuth",
  custom_oauth: "Custom OAuth",
} as const

const integrationSectionOrder: IntegrationSectionType[] = [
  "oauth",
  "custom_oauth",
]

const integrationSectionTitles: Record<IntegrationSectionType, string> = {
  oauth: "OAuth",
  custom_oauth: "Custom OAuth",
}

function getIntegrationStatus(item: IntegrationItem): IntegrationStatus {
  return displayStatus(item.integration_status)
}

function getIntegrationDisplayType(
  item: IntegrationItem
): IntegrationSectionType {
  if (item.id.startsWith("custom_")) {
    return "custom_oauth"
  }
  return item.type
}

export default function IntegrationsPage() {
  const workspaceId = useWorkspaceId()
  const canReadIntegrations = useScopeCheck("integration:read")
  const canUpdateIntegrations = useScopeCheck("integration:update")
  const canMutateIntegrations = canUpdateIntegrations === true
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
  const [disconnectTarget, setDisconnectTarget] = useState<{
    providerId: string
    grantType: OAuthGrantType
    name: string
  } | null>(null)
  const [expandedSections, setExpandedSections] = useState<
    Record<IntegrationSectionType, boolean>
  >({
    oauth: false,
    custom_oauth: false,
  })
  const lastHandledConnectRef = useRef<string | null>(null)

  const { providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)

  function handleTypeFilterToggle(filter: IntegrationTypeFilter) {
    setTypeFilters((prev) =>
      prev.includes(filter)
        ? prev.filter((value) => value !== filter)
        : [...prev, filter]
    )
  }

  const connectProviderMutation = useConnectProvider(workspaceId)
  const disconnectProviderMutation = useDisconnectProvider(workspaceId)
  const testConnectionMutation = useTestProvider(workspaceId)

  const allIntegrations = useMemo<IntegrationItem[]>(() => {
    return (
      providers
        ?.filter((provider) => !isMcpProvider(provider.id))
        .map<IntegrationItem>((provider) => ({
          type: "oauth" as const,
          id: provider.id,
          name: provider.name,
          description: provider.description,
          enabled: provider.enabled,
          integration_status: provider.integration_status,
          grant_type: provider.grant_type,
          requires_config: provider.requires_config,
        })) ?? []
    )
  }, [providers])

  const filteredIntegrations = useMemo(() => {
    const q = searchQuery.toLowerCase()
    const filtered = allIntegrations.filter((item) => {
      const matchesSearch =
        item.name.toLowerCase().includes(q) ||
        (item.description ?? "").toLowerCase().includes(q)
      const matchesType =
        typeFilters.length === 0 ||
        typeFilters.some((filter) => {
          if (filter === "custom_oauth") {
            return item.id.startsWith("custom_")
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

      if (a.enabled !== b.enabled) {
        return a.enabled ? -1 : 1
      }

      return a.name.localeCompare(b.name)
    })
  }, [allIntegrations, connectionFilter, searchQuery, typeFilters])

  const sectionedIntegrations = useMemo(() => {
    const groupedIntegrations: Record<
      IntegrationSectionType,
      IntegrationItem[]
    > = {
      oauth: [],
      custom_oauth: [],
    }

    for (const item of filteredIntegrations) {
      groupedIntegrations[getIntegrationDisplayType(item)].push(item)
    }

    return integrationSectionOrder
      .map((sectionType) => ({
        sectionType,
        title: integrationSectionTitles[sectionType],
        items: groupedIntegrations[sectionType],
      }))
      .filter(
        (section) =>
          section.items.length > 0 || section.sectionType === "custom_oauth"
      )
  }, [filteredIntegrations])

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
    if (!canMutateIntegrations) {
      return
    }
    if (!connectParam || !providers) {
      return
    }

    const handleKey = `${connectParam}:${connectGrantType ?? ""}`
    if (lastHandledConnectRef.current === handleKey) {
      return
    }

    const provider = providers.find(
      (item) =>
        item.id === connectParam &&
        (connectGrantType == null || item.grant_type === connectGrantType)
    )
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
    canMutateIntegrations,
    clearConnectParams,
    connectGrantType,
    connectParam,
    handleDirectConnect,
    handleOpenOAuthModal,
    providers,
  ])

  const handleReconnect = useCallback(
    (providerId: string, grantType: OAuthGrantType) => {
      handleDirectConnect(providerId, grantType)
    },
    [handleDirectConnect]
  )

  if (
    canReadIntegrations === undefined ||
    canUpdateIntegrations === undefined ||
    providersIsLoading
  ) {
    return <CenteredSpinner />
  }

  if (!canReadIntegrations) {
    return null
  }
  if (providersError) {
    return <div>Error: {providersError.message}</div>
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
        displayIntegrationCount={filteredIntegrations.length}
      />

      {/* Integrations List */}
      <ScrollArea className="flex-1 min-h-0 [&>[data-radix-scroll-area-viewport]]:[scrollbar-width:none] [&>[data-radix-scroll-area-viewport]::-webkit-scrollbar]:hidden [&>[data-orientation=vertical]]:!hidden [&>[data-orientation=horizontal]]:!hidden">
        <div className="w-full pb-10">
          {sectionedIntegrations.map((section) => {
            const isExpanded = expandedSections[section.sectionType] ?? false
            return (
              <Collapsible
                key={section.sectionType}
                className="border-b border-border/50"
                open={isExpanded}
                onOpenChange={(nextOpen) =>
                  setExpandedSections((prev) => ({
                    ...prev,
                    [section.sectionType]: nextOpen,
                  }))
                }
              >
                <div>
                  <CollapsibleTrigger asChild>
                    <button
                      type="button"
                      className="flex w-full items-center gap-1 py-1.5 pl-[10px] pr-3 text-left transition-colors hover:bg-muted/50 data-[state=open]:bg-primary/5 dark:data-[state=open]:bg-primary/10 [&[data-state=open]_.chevron]:rotate-90"
                    >
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                        <ChevronRight className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-medium">
                          {section.title}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {section.items.length}
                        </span>
                      </div>
                    </button>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div className="divide-y divide-border/50">
                      {section.items.map((item) => {
                        const status = getIntegrationStatus(item)
                        const isConnected =
                          item.integration_status === "connected"
                        const isConfigured = status === "connected"
                        const isClickable =
                          isConnected ||
                          (canMutateIntegrations &&
                            item.requires_config &&
                            item.enabled)
                        const isDisabled = !item.enabled
                        const showConnect =
                          canMutateIntegrations && !isConnected
                        const showDisconnect =
                          canMutateIntegrations && isConnected
                        const isConnecting =
                          (connectProviderMutation.isPending &&
                            connectProviderMutation.variables?.providerId ===
                              item.id) ||
                          (testConnectionMutation.isPending &&
                            testConnectionMutation.variables?.providerId ===
                              item.id)
                        const isDisconnecting =
                          disconnectProviderMutation.isPending &&
                          disconnectProviderMutation.variables?.providerId ===
                            item.id
                        const displayType = getIntegrationDisplayType(item)
                        const typeLabel = integrationTypeLabels[displayType]

                        return (
                          <Item
                            key={`${item.id}-${item.grant_type}`}
                            variant="default"
                            size="sm"
                            className={cn(
                              "w-full flex-nowrap rounded-none border-none px-3 py-1.5 text-left transition-colors hover:bg-muted/50",
                              isClickable && "cursor-pointer",
                              !isClickable && "cursor-default"
                            )}
                            onClick={() => {
                              if (isConnected) {
                                setDetailsProvider({
                                  providerId: item.id,
                                  grantType: item.grant_type,
                                })
                                return
                              }
                              if (item.requires_config && item.enabled) {
                                if (!canMutateIntegrations) {
                                  return
                                }
                                handleOpenOAuthModal(item.id, item.grant_type)
                              }
                            }}
                          >
                            <ItemMedia className="translate-y-0 self-center">
                              <ProviderIcon
                                providerId={item.id}
                                className="size-6 rounded"
                              />
                            </ItemMedia>
                            <ItemContent className="min-w-0 gap-0">
                              <ItemTitle className="flex w-full min-w-0 items-center gap-2 text-xs">
                                <TooltipProvider>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <span className="min-w-0 truncate">
                                        {item.name}
                                      </span>
                                    </TooltipTrigger>
                                    <TooltipContent>
                                      <p>{item.name}</p>
                                    </TooltipContent>
                                  </Tooltip>
                                </TooltipProvider>
                                <span className="text-xs text-muted-foreground">
                                  {typeLabel}
                                </span>
                              </ItemTitle>
                            </ItemContent>
                            <ItemActions className="ml-auto flex shrink-0 items-center gap-1.5 pl-3">
                              {isConfigured ? (
                                <TooltipProvider>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <span className="flex h-6 w-6 items-center justify-center">
                                        <SquareAsterisk className="icon-success size-3.5" />
                                      </span>
                                    </TooltipTrigger>
                                    <TooltipContent>
                                      <p>Configured</p>
                                    </TooltipContent>
                                  </Tooltip>
                                </TooltipProvider>
                              ) : null}
                              {isConnected && canMutateIntegrations && (
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
                                          handleReconnect(
                                            item.id,
                                            item.grant_type
                                          )
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
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-6 border-input bg-white px-2.5 text-[11px] text-foreground hover:bg-muted"
                                    onClick={(event) => {
                                      event.stopPropagation()
                                      if (item.requires_config) {
                                        handleOpenOAuthModal(
                                          item.id,
                                          item.grant_type
                                        )
                                        return
                                      }
                                      handleDirectConnect(
                                        item.id,
                                        item.grant_type
                                      )
                                    }}
                                    disabled={isDisabled || isConnecting}
                                  >
                                    {isConnecting ? (
                                      <Loader2 className="mr-1.5 size-3 animate-spin" />
                                    ) : null}
                                    Connect
                                  </Button>
                                </>
                              )}
                              {showDisconnect && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="h-6 border-input bg-white px-2.5 text-[11px] text-foreground hover:border-destructive hover:bg-destructive hover:text-destructive-foreground"
                                  onClick={(event) => {
                                    event.stopPropagation()
                                    setDisconnectTarget({
                                      providerId: item.id,
                                      grantType: item.grant_type,
                                      name: item.name,
                                    })
                                  }}
                                  disabled={isDisabled || isDisconnecting}
                                >
                                  Disconnect
                                </Button>
                              )}
                            </ItemActions>
                          </Item>
                        )
                      })}
                    </div>
                  </CollapsibleContent>
                </div>
              </Collapsible>
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
          canUpdate={canMutateIntegrations}
        />
      )}
      <ConfirmDestructiveDialog
        open={disconnectTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDisconnectTarget(null)
        }}
        confirmPhrase={disconnectTarget?.name ?? ""}
        title="Disconnect integration"
        description={
          disconnectTarget ? (
            <>
              Are you sure you want to disconnect from{" "}
              <span className="font-medium">{disconnectTarget.name}</span>?
            </>
          ) : null
        }
        confirmLabel="Disconnect"
        isPending={disconnectProviderMutation.isPending}
        onConfirm={async () => {
          if (!disconnectTarget) return
          await disconnectProviderMutation.mutateAsync({
            providerId: disconnectTarget.providerId,
            grantType: disconnectTarget.grantType,
          })
          setDisconnectTarget(null)
        }}
      />
    </div>
  )
}
