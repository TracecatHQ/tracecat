"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowRight, ExternalLink, Loader2, Lock, Sparkles } from "lucide-react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useEffect, useMemo, useState } from "react"
import {
  mcpIntegrationsConnectPlatformMcpCatalog,
  mcpIntegrationsDeleteMcpIntegration,
  mcpIntegrationsListMcpIntegrations,
  mcpIntegrationsListPlatformMcpCatalog,
} from "@/client/services.gen"
import type {
  MCPConnectionSpec,
  MCPIntegrationRead,
  PlatformMCPCatalogListResponse,
  PlatformMCPCatalogRead,
} from "@/client/types.gen"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CatalogHeader } from "@/components/catalog/catalog-header"
import { getMcpProviderIconId, ProviderIcon } from "@/components/icons"
import { MCPIntegrationDialog } from "@/components/integrations/mcp-integration-dialog"
import { OAuthIntegrationDialog } from "@/components/integrations/oauth-integration-dialog"
import { LockedFeatureModal } from "@/components/locked-feature-modal"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { toast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  getMcpOAuthConnectErrorDetail,
  type TracecatApiError,
} from "@/lib/errors"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const CREATE_MCP_SERVER_PARAM = "createMcpServer"
const ALL_CATEGORY = "All"
const CUSTOM_CATEGORY = "Custom"
const MCP_CATEGORIES = [
  "SIEM / Datalake",
  "Endpoint",
  "Threat Intelligence",
  "Identity",
  "Cloud",
  "Network",
  "Ticketing",
  "On-Call",
  "Communication",
  "Email",
  "AppSec",
  "Compliance",
  "Observability",
  "Source Control",
  "IaC",
  "Productivity",
  CUSTOM_CATEGORY,
]

interface CatalogItem {
  kind: "catalog" | "workspace"
  entry: PlatformMCPCatalogRead
}

function connectionSpecFromIntegration(
  integration: MCPIntegrationRead
): MCPConnectionSpec {
  const base = {
    requires_config: false,
    config_fields: [],
    credentials: [],
  }
  if (integration.server_type === "stdio") {
    const stdio = {
      ...base,
      server_type: "stdio" as const,
      stdio_command: integration.stdio_command,
      stdio_args: integration.stdio_args ?? [],
      stdio_env: [],
      packages: [],
    }
    // No stdio_oauth2 kind: MCP OAuth is HTTP-only, stdio rows never carry it.
    if (integration.auth_type === "CUSTOM") {
      return { ...stdio, kind: "stdio_custom", auth_type: "CUSTOM" }
    }
    return { ...stdio, kind: "stdio_none", auth_type: "NONE" }
  }

  const serverUri = integration.server_uri ?? ""
  if (integration.auth_type === "OAUTH2") {
    return {
      ...base,
      kind: "http_oauth2",
      server_type: "http",
      auth_type: "OAUTH2",
      server_uri: serverUri,
      scopes: [],
    }
  }
  if (integration.auth_type === "CUSTOM") {
    return {
      ...base,
      kind: "http_custom",
      server_type: "http",
      auth_type: "CUSTOM",
      server_uri: serverUri,
    }
  }
  return {
    ...base,
    kind: "http_none",
    server_type: "http",
    auth_type: "NONE",
    server_uri: serverUri,
  }
}

function workspaceIntegrationToCatalogEntry(
  integration: MCPIntegrationRead
): PlatformMCPCatalogRead {
  return {
    id: integration.id,
    slug: integration.slug,
    name: integration.name,
    description:
      integration.description ||
      (integration.server_type === "stdio"
        ? "Custom stdio MCP server"
        : integration.server_uri || "Custom MCP server"),
    category: CUSTOM_CATEGORY,
    status: "available",
    icon_url: null,
    docs_url: null,
    provider_id: "custom",
    connection_spec: connectionSpecFromIntegration(integration),
    connection_options: [],
    locked: false,
    state: integration.state,
    mcp_integration_id: integration.id,
    mcp_server_type: integration.server_type,
    mcp_auth_type: integration.auth_type,
    created_at: integration.created_at,
    updated_at: integration.updated_at,
    last_refreshed_at: null,
  }
}

function workspaceIntegrationMatches(
  integration: MCPIntegrationRead,
  query: string
) {
  if (!query) {
    return true
  }
  const haystack = [
    integration.name,
    integration.description,
    integration.slug,
    integration.server_uri,
    integration.stdio_command,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
  return haystack.includes(query)
}

function catalogConnectionSpecs(entry: PlatformMCPCatalogRead) {
  return entry.connection_options?.length
    ? entry.connection_options.map((option) => option.connection_spec)
    : entry.connection_spec
      ? [entry.connection_spec]
      : []
}

function catalogTransports(entry: PlatformMCPCatalogRead) {
  return Array.from(
    new Set(catalogConnectionSpecs(entry).map((spec) => spec.server_type))
  )
}

function isProviderBackedOAuth(entry: PlatformMCPCatalogRead) {
  return Boolean(
    entry.provider_id &&
      catalogConnectionSpecs(entry).some((spec) => spec.auth_type === "OAUTH2")
  )
}

function requiresCatalogConfig(entry: PlatformMCPCatalogRead) {
  return catalogConnectionSpecs(entry).some((spec) => spec.requires_config)
}

function isCatalogEntryConnectable(entry: PlatformMCPCatalogRead) {
  return Boolean(
    entry.connection_spec ||
      (entry.connection_options && entry.connection_options.length > 0) ||
      isProviderBackedOAuth(entry)
  )
}

export default function McpServersPage() {
  const workspaceId = useWorkspaceId()
  const canRead = useScopeCheck("integration:read")
  const canCreate = useScopeCheck("integration:create")
  const canUpdate = useScopeCheck("integration:update")
  const canDelete = useScopeCheck("integration:delete")
  const canCreateMcp = canCreate === true
  const canUpdateIntegrations = canUpdate === true
  const canDeleteMcp = canDelete === true
  const { hasEntitlement } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  const [searchQuery, setSearchQuery] = useState("")
  const [activeCategory, setActiveCategory] = useState<string>(ALL_CATEGORY)
  const [createOpen, setCreateOpen] = useState(false)
  const [configEntry, setConfigEntry] = useState<PlatformMCPCatalogRead | null>(
    null
  )
  const [lockedCatalogEntry, setLockedCatalogEntry] =
    useState<PlatformMCPCatalogRead | null>(null)
  const [providerConfigEntry, setProviderConfigEntry] =
    useState<PlatformMCPCatalogRead | null>(null)
  const [editingItem, setEditingItem] = useState<CatalogItem | null>(null)

  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const createSignal = searchParams?.get(CREATE_MCP_SERVER_PARAM) ?? null
  const queryClient = useQueryClient()
  const catalogQueryKey = [
    "mcp-catalog",
    workspaceId,
    searchQuery,
    activeCategory,
  ] as const
  const workspaceMcpQueryKey = [
    "mcp-integrations",
    workspaceId,
    "workspace",
  ] as const

  const {
    data: catalogData,
    isLoading: catalogIsLoading,
    error: catalogError,
  } = useQuery<PlatformMCPCatalogListResponse, TracecatApiError>({
    queryKey: catalogQueryKey,
    queryFn: async () =>
      await mcpIntegrationsListPlatformMcpCatalog({
        workspaceId,
        q: searchQuery.trim() || undefined,
        category:
          activeCategory === ALL_CATEGORY || activeCategory === CUSTOM_CATEGORY
            ? undefined
            : activeCategory,
        limit: 100,
      }),
    enabled: Boolean(
      workspaceId && canRead === true && activeCategory !== CUSTOM_CATEGORY
    ),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
  const {
    data: workspaceMcpIntegrations,
    isLoading: workspaceMcpIsLoading,
    error: workspaceMcpError,
  } = useQuery<MCPIntegrationRead[], TracecatApiError>({
    queryKey: workspaceMcpQueryKey,
    queryFn: async () =>
      await mcpIntegrationsListMcpIntegrations({
        workspaceId,
        source: "workspace",
      }),
    enabled: Boolean(workspaceId && canRead === true),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
  const catalogConnectMutation = useMutation({
    mutationFn: async (entry: PlatformMCPCatalogRead) =>
      await mcpIntegrationsConnectPlatformMcpCatalog({
        workspaceId,
        catalogSlug: entry.slug,
      }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: catalogQueryKey })
      queryClient.invalidateQueries({
        queryKey: ["mcp-integrations", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: workspaceMcpQueryKey })
      if (result.auth_url) {
        window.location.href = result.auth_url
        return
      }
      if (!result.mcp_integration) {
        return
      }
      toast({
        title: "MCP server connected",
        description: `Added ${result.mcp_integration.name}`,
      })
    },
    onError: (error) => {
      const apiError = error as TracecatApiError
      toast({
        title: "Failed to connect MCP server",
        description: getMcpOAuthConnectErrorDetail(apiError),
        variant: "destructive",
      })
    },
  })
  const catalogDisconnectMutation = useMutation({
    mutationFn: async (entry: PlatformMCPCatalogRead) => {
      if (!entry.mcp_integration_id) {
        throw new Error("No MCP integration to disconnect")
      }
      await mcpIntegrationsDeleteMcpIntegration({
        workspaceId,
        mcpIntegrationId: entry.mcp_integration_id,
      })
      return entry
    },
    onSuccess: (entry) => {
      queryClient.invalidateQueries({ queryKey: catalogQueryKey })
      queryClient.invalidateQueries({
        queryKey: ["mcp-integrations", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: workspaceMcpQueryKey })
      toast({
        title: "MCP server disconnected",
        description: `Disconnected ${entry.name}`,
      })
    },
    onError: (error) => {
      const apiError = error as TracecatApiError
      toast({
        title: "Failed to disconnect MCP server",
        description: String(apiError.body?.detail ?? apiError.message),
        variant: "destructive",
      })
    },
  })

  useEffect(() => {
    if (!createSignal || !pathname) {
      return
    }
    if (canCreate === undefined) {
      return
    }
    if (canCreate === true) {
      setCreateOpen(true)
    }
    const params = new URLSearchParams(searchParams?.toString() ?? "")
    params.delete(CREATE_MCP_SERVER_PARAM)
    const next = params.toString()
    router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canCreate, createSignal, pathname, router])

  const items = useMemo<CatalogItem[]>(() => {
    const catalogMcpIntegrationIds = new Set(
      (catalogData?.items ?? [])
        .map((entry) => entry.mcp_integration_id)
        .filter((id): id is string => Boolean(id))
    )
    const catalogItems =
      activeCategory === CUSTOM_CATEGORY
        ? []
        : (catalogData?.items ?? []).map((entry) => ({
            kind: "catalog" as const,
            entry,
          }))
    const normalizedSearch = searchQuery.trim().toLowerCase()
    const workspaceItems =
      activeCategory === ALL_CATEGORY || activeCategory === CUSTOM_CATEGORY
        ? (workspaceMcpIntegrations ?? [])
            .filter(
              (integration) => !catalogMcpIntegrationIds.has(integration.id)
            )
            .filter((integration) =>
              workspaceIntegrationMatches(integration, normalizedSearch)
            )
            .map((integration) => ({
              kind: "workspace" as const,
              entry: workspaceIntegrationToCatalogEntry(integration),
            }))
        : []
    return [...catalogItems, ...workspaceItems]
  }, [
    activeCategory,
    catalogData?.items,
    searchQuery,
    workspaceMcpIntegrations,
  ])

  const totalCount = items.length

  if (canRead === undefined) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (canRead === false) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <Alert className="max-w-md">
          <AlertTitle>Access denied</AlertTitle>
          <AlertDescription>
            You do not have permission to view MCP servers.
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  function handleConnect(item: CatalogItem) {
    const { entry } = item
    if (entry.locked) {
      setLockedCatalogEntry(entry)
      return
    }
    const connected = entry.state === "connected"
    const connectable = isCatalogEntryConnectable(entry)

    if (entry.mcp_integration_id && connected) {
      if (canDeleteMcp) {
        catalogDisconnectMutation.mutate(entry)
      }
      return
    }

    if (item.kind === "workspace" && entry.mcp_integration_id) {
      setEditingItem(item)
      return
    }

    // Migrated catalog rows can be disconnected without the entitlement, but
    // reconnecting through the platform catalog requires the upgrade.
    if (entry.mcp_integration_id && !agentAddonsEnabled) {
      setLockedCatalogEntry(entry)
      return
    }

    if (!connectable) {
      toast({
        title: `${entry.name} is coming soon`,
        description: "This MCP server isn't connectable yet.",
      })
      return
    }

    if (!canCreateMcp) {
      return
    }

    // A configured catalog row already exists (e.g. OAuth was started but not
    // completed); reconnect through the backend so it reuses the row instead
    // of creating a duplicate via the config dialog.
    if (entry.mcp_integration_id) {
      catalogConnectMutation.mutate(entry)
      return
    }

    if (requiresCatalogConfig(entry)) {
      setConfigEntry(entry)
      return
    }

    catalogConnectMutation.mutate(entry)
  }

  function handleConfigure(item: CatalogItem) {
    const { entry } = item
    if (entry.locked) {
      setLockedCatalogEntry(entry)
      return
    }
    if (entry.mcp_integration_id) {
      setEditingItem(item)
      return
    }
    if (requiresCatalogConfig(entry) && canCreateMcp) {
      setConfigEntry(entry)
      return
    }
    if (isProviderBackedOAuth(entry) && entry.provider_id && canCreateMcp) {
      setProviderConfigEntry(entry)
      return
    }
    if (entry.connection_spec && canCreateMcp) {
      setConfigEntry(entry)
      return
    }
  }

  function isCatalogConnectPending(entry: PlatformMCPCatalogRead) {
    return (
      catalogConnectMutation.isPending &&
      catalogConnectMutation.variables?.slug === entry.slug
    )
  }

  function isDisconnectPending(item: CatalogItem) {
    return (
      catalogDisconnectMutation.isPending &&
      catalogDisconnectMutation.variables?.slug === item.entry.slug
    )
  }

  function isActionPending(item: CatalogItem) {
    return isCatalogConnectPending(item.entry) || isDisconnectPending(item)
  }

  const categories = [ALL_CATEGORY, ...MCP_CATEGORIES]

  return (
    <div className="flex h-full flex-col">
      <CatalogHeader
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="Search MCP servers..."
        pillFilters={categories.map((category) => ({
          value: category,
          label: category,
        }))}
        activePillFilters={[activeCategory]}
        onPillFilterToggle={(category) => setActiveCategory(category)}
        displayCount={totalCount}
        countLabel={`server${totalCount === 1 ? "" : "s"}`}
      />

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {catalogError ? (
          <Alert variant="destructive" className="mb-4">
            <AlertTitle>Failed to load MCP catalog</AlertTitle>
            <AlertDescription>
              {String(catalogError.body?.detail ?? catalogError.message)}
            </AlertDescription>
          </Alert>
        ) : null}
        {workspaceMcpError ? (
          <Alert variant="destructive" className="mb-4">
            <AlertTitle>Failed to load workspace MCP servers</AlertTitle>
            <AlertDescription>
              {String(
                workspaceMcpError.body?.detail ?? workspaceMcpError.message
              )}
            </AlertDescription>
          </Alert>
        ) : null}

        {catalogIsLoading || workspaceMcpIsLoading ? (
          <div className="flex h-32 items-center justify-center">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : totalCount === 0 ? (
          <Card className="flex flex-col items-center gap-3 p-8 text-center">
            <Sparkles className="size-8 text-muted-foreground" />
            <div>
              <h2 className="text-sm font-semibold">No MCP servers found</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Try a different search or category.
              </p>
            </div>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {items.map((item) => (
              <McpCatalogCard
                key={`${item.kind}:${item.entry.mcp_integration_id ?? item.entry.slug}`}
                item={item}
                canCreate={canCreateMcp}
                canUpdate={canUpdateIntegrations}
                canDelete={canDeleteMcp}
                isActionPending={isActionPending(item)}
                isDisconnecting={isDisconnectPending(item)}
                reconnectLocked={
                  item.kind === "catalog" &&
                  !item.entry.locked &&
                  Boolean(item.entry.mcp_integration_id) &&
                  item.entry.state !== "connected" &&
                  !agentAddonsEnabled
                }
                onConnect={() => handleConnect(item)}
                onConfigure={() => handleConfigure(item)}
              />
            ))}
          </div>
        )}
      </div>

      {createOpen ? (
        <MCPIntegrationDialog
          open
          onOpenChange={(next) => {
            setCreateOpen(next)
            if (!next) {
              queryClient.invalidateQueries({ queryKey: catalogQueryKey })
              queryClient.invalidateQueries({ queryKey: workspaceMcpQueryKey })
            }
          }}
          hideTrigger
        />
      ) : null}

      <LockedFeatureModal
        open={lockedCatalogEntry !== null}
        onOpenChange={(next) => {
          if (!next) {
            setLockedCatalogEntry(null)
          }
        }}
        title="Upgrade to unlock this feature"
        description="Tracecat-managed MCP catalog connectors are included with Enterprise. To use your own setup, create a custom MCP server."
        bullets={[]}
        hideFooter
      />

      {configEntry ? (
        <MCPIntegrationDialog
          open
          onOpenChange={(next) => {
            if (!next) {
              setConfigEntry(null)
              queryClient.invalidateQueries({ queryKey: catalogQueryKey })
              queryClient.invalidateQueries({ queryKey: workspaceMcpQueryKey })
            }
          }}
          catalogEntry={configEntry}
          hideTrigger
        />
      ) : null}

      {providerConfigEntry?.provider_id ? (
        <OAuthIntegrationDialog
          open
          onOpenChange={(next) => {
            if (!next) {
              setProviderConfigEntry(null)
              queryClient.invalidateQueries({ queryKey: catalogQueryKey })
              queryClient.invalidateQueries({ queryKey: workspaceMcpQueryKey })
            }
          }}
          providerId={providerConfigEntry.provider_id}
          grantType="authorization_code"
        />
      ) : null}

      {editingItem?.entry.mcp_integration_id ? (
        <MCPIntegrationDialog
          open
          onOpenChange={(next) => {
            if (!next) {
              setEditingItem(null)
              queryClient.invalidateQueries({ queryKey: catalogQueryKey })
              queryClient.invalidateQueries({ queryKey: workspaceMcpQueryKey })
            }
          }}
          mcpIntegrationId={editingItem.entry.mcp_integration_id}
          catalogEntry={
            editingItem.kind === "catalog" ? editingItem.entry : null
          }
          hideTrigger
        />
      ) : null}
    </div>
  )
}

interface McpCatalogCardProps {
  item: CatalogItem
  canCreate: boolean
  canUpdate: boolean
  canDelete: boolean
  isActionPending: boolean
  isDisconnecting: boolean
  reconnectLocked: boolean
  onConnect: () => void
  onConfigure: () => void
}

function McpCatalogCard({
  item,
  canCreate,
  canUpdate,
  canDelete,
  isActionPending,
  isDisconnecting,
  reconnectLocked,
  onConnect,
  onConfigure,
}: McpCatalogCardProps) {
  const { entry } = item
  const locked = entry.locked === true
  const hasMcpRow = Boolean(entry.mcp_integration_id)
  const connected = entry.state === "connected"
  const configured = !connected && (entry.state === "configured" || hasMcpRow)
  const hasWorkspaceConfig = configured || connected
  const connectable = isCatalogEntryConnectable(entry)
  // Rows with a workspace integration stay actionable even when the catalog
  // response hides connection specs (e.g. unentitled with a migrated row).
  const comingSoon =
    !locked && !hasMcpRow && (entry.status === "coming_soon" || !connectable)
  const specTransports = catalogTransports(entry)
  const transports =
    specTransports.length > 0
      ? specTransports
      : entry.mcp_server_type
        ? [entry.mcp_server_type]
        : []
  const docsUrl = locked ? null : entry.docs_url
  const disconnectable = connected && hasMcpRow
  let actionLabel = "Connect"
  if (disconnectable) {
    actionLabel = "Disconnect"
  } else if (configured) {
    actionLabel = "Reconnect"
  }
  const canManage = entry.mcp_integration_id ? canUpdate : false
  let canAct = false
  if (locked || reconnectLocked) {
    canAct = true
  } else if (disconnectable) {
    canAct = canDelete
  } else if (item.kind === "workspace" && configured) {
    canAct = canManage
  } else if (connectable) {
    canAct = canCreate
  }
  let canConfigure = false
  if (locked) {
    canConfigure = true
  } else if (hasWorkspaceConfig) {
    canConfigure = canManage
  } else if (isCatalogEntryConnectable(entry)) {
    canConfigure = canCreate
  }
  const configureLabel = "Configure"
  let statusLabel = "Not connected"
  let statusClassName = "border-muted bg-muted/30 text-muted-foreground"
  if (isActionPending) {
    statusLabel = isDisconnecting ? "Disconnecting" : "Connecting"
    statusClassName = "border-blue-200 bg-blue-50 text-blue-700"
  } else if (locked) {
    statusLabel = "Locked"
    statusClassName = "border-muted bg-muted/30 text-muted-foreground"
  } else if (connected) {
    statusLabel = "Connected"
    statusClassName = "border-emerald-200 bg-emerald-50 text-emerald-700"
  } else if (configured) {
    statusLabel = "Configured"
    statusClassName = "border-blue-200 bg-blue-50 text-blue-700"
  } else if (comingSoon) {
    statusLabel = "Coming soon"
    statusClassName = "border-muted bg-muted/30 text-muted-foreground"
  }
  let buttonLabel = actionLabel
  if (comingSoon) {
    buttonLabel = "Coming soon"
  } else if (locked) {
    buttonLabel = "Connect"
  }
  if (isActionPending) {
    buttonLabel = statusLabel
  }
  const actionLocked = locked || reconnectLocked
  let actionClassName = "text-blue-600 hover:text-blue-700"
  if (actionLocked) {
    actionClassName = "text-muted-foreground hover:text-foreground"
  } else if (disconnectable) {
    actionClassName = "text-destructive hover:text-destructive"
  }

  return (
    <Card
      role={locked ? "button" : undefined}
      tabIndex={locked ? 0 : undefined}
      onClick={locked ? onConnect : undefined}
      onKeyDown={
        locked
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                onConnect()
              }
            }
          : undefined
      }
      className={cn(
        "flex h-full min-h-[132px] flex-col gap-2.5 border bg-card p-4 shadow-none transition-colors hover:border-foreground/30",
        locked && "cursor-pointer"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <ProviderIcon
          providerId={getMcpProviderIconId(entry.provider_id ?? entry.slug)}
          className={cn("size-9 shrink-0", locked && "opacity-50 grayscale")}
        />

        <div className="flex flex-wrap justify-end gap-1">
          {transports.map((transport) => (
            <Badge
              key={transport}
              variant="outline"
              className="h-4 px-1.5 text-[10px] uppercase tracking-wide"
            >
              {transport}
            </Badge>
          ))}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <h3 className="truncate text-sm font-semibold leading-5 text-foreground">
            {entry.name}
          </h3>
          <Badge
            variant="outline"
            className={cn(
              "h-5 shrink-0 gap-1 px-1.5 text-[10px] font-medium",
              statusClassName
            )}
          >
            {isActionPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : null}
            {!isActionPending && locked ? <Lock className="size-3" /> : null}
            {statusLabel}
          </Badge>
        </div>
        <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">
          {entry.description}
        </p>
        {docsUrl ? (
          <a
            href={docsUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
            className="mt-0.5 inline-flex w-fit items-center gap-1 text-xs text-blue-600 hover:underline"
          >
            View docs
            <ExternalLink className="size-3" />
          </a>
        ) : null}
      </div>
      <div className="mt-auto flex items-center justify-between gap-2 pt-2">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="-ml-2 h-7 px-2 text-xs font-medium text-muted-foreground shadow-none hover:text-foreground"
          disabled={!canConfigure || isActionPending}
          onClick={(event) => {
            event.stopPropagation()
            onConfigure()
          }}
        >
          {configureLabel}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className={cn(
            "-mr-2 h-7 gap-1 px-2 text-xs font-medium shadow-none hover:bg-transparent focus:bg-transparent focus-visible:bg-transparent active:bg-transparent disabled:text-muted-foreground",
            actionClassName
          )}
          disabled={comingSoon || !canAct || isActionPending}
          onClick={(event) => {
            event.stopPropagation()
            onConnect()
          }}
        >
          {isActionPending ? (
            <Loader2 className="mr-1.5 size-3.5 animate-spin" />
          ) : null}
          {buttonLabel}
          {!isActionPending && !comingSoon && !disconnectable ? (
            <ArrowRight className="size-3.5" />
          ) : null}
        </Button>
      </div>
    </Card>
  )
}
