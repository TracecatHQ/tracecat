"use client"

import { Search } from "lucide-react"
import { useRouter } from "next/navigation"
import { useMemo, useState } from "react"
import type { IntegrationStatus, OAuthGrantType } from "@/client"
import { ProviderIcon } from "@/components/icons"
import { MCPIntegrationDialog } from "@/components/integrations/mcp-integration-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Input } from "@/components/ui/input"
import { Item, ItemContent, ItemMedia, ItemTitle } from "@/components/ui/item"
import { useIntegrations, useListMcpIntegrations } from "@/lib/hooks"
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
    }
  | {
      type: "mcp"
      id: string
      name: string
      description: string | null
      slug: string
      server_uri: string
      auth_type: string
    }

export default function IntegrationsPage() {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const [searchQuery, setSearchQuery] = useState("")

  const { integrations, providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)
  const { mcpIntegrations, mcpIntegrationsIsLoading, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId)

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

    // Sort: Connected first, then Configured, then Available/Not Configured
    // Within each group, sort by enabled status (for OAuth), then alphabetically
    return [...filtered].sort((a, b) => {
      // MCP integrations are always "connected" (they exist)
      const aStatus: IntegrationStatus =
        a.type === "mcp" ? "connected" : a.integration_status
      const bStatus: IntegrationStatus =
        b.type === "mcp" ? "connected" : b.integration_status

      // Status priority: connected > configured > not_configured
      const statusOrder = {
        connected: 0,
        configured: 1,
        not_configured: 2,
      }

      const aOrder = statusOrder[aStatus] ?? 2
      const bOrder = statusOrder[bStatus] ?? 2

      if (aOrder !== bOrder) {
        return aOrder - bOrder
      }

      // Within same status, sort by enabled first (OAuth only)
      if (a.type === "oauth" && b.type === "oauth") {
        if (a.enabled !== b.enabled) {
          return a.enabled ? -1 : 1
        }
      }

      // Finally, sort alphabetically
      return a.name.localeCompare(b.name)
    })
  }, [allIntegrations, searchQuery])

  const handleIntegrationClick = (item: IntegrationItem) => {
    if (item.type === "oauth" && item.enabled) {
      router.push(
        `/workspaces/${workspaceId}/integrations/${item.id}?tab=overview&grant_type=${item.grant_type}`
      )
    } else if (item.type === "mcp") {
      router.push(`/workspaces/${workspaceId}/integrations/mcp/${item.id}`)
    }
  }

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
            item.type === "mcp" ? "connected" : item.integration_status
          const isClickable =
            (isOAuth && item.enabled) || (!isOAuth && item.type === "mcp")

          return (
            <Item
              key={isOAuth ? `${item.id}-${item.grant_type}` : item.id}
              variant="outline"
              className={cn(
                isClickable
                  ? "cursor-pointer hover:bg-muted/50"
                  : "cursor-not-allowed opacity-60"
              )}
              onClick={() => handleIntegrationClick(item)}
            >
              <ItemMedia>
                {isOAuth ? (
                  <ProviderIcon
                    providerId={item.id}
                    className="size-7 rounded"
                  />
                ) : (
                  <div className="flex size-7 items-center justify-center rounded bg-muted text-xs font-medium text-muted-foreground">
                    MCP
                  </div>
                )}
              </ItemMedia>
              <ItemContent>
                <ItemTitle className="text-sm">{item.name}</ItemTitle>
              </ItemContent>
              <span
                className={cn(
                  "size-1.5 shrink-0 rounded-full",
                  isOAuth &&
                    item.enabled &&
                    status === "connected" &&
                    "bg-green-500",
                  isOAuth &&
                    item.enabled &&
                    status === "configured" &&
                    "bg-yellow-500",
                  isOAuth &&
                    item.enabled &&
                    status === "not_configured" &&
                    "bg-gray-400",
                  isOAuth && !item.enabled && "bg-gray-300",
                  !isOAuth && "bg-green-500"
                )}
              />
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
      <MCPIntegrationDialog hideTrigger />
    </div>
  )
}
