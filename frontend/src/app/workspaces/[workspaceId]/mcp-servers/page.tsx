"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { formatDistanceToNowStrict } from "date-fns"
import { Globe, Loader2, Plus, Sparkles, Terminal } from "lucide-react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  MCPIntegrationRead,
  OAuthGrantType,
  ProviderReadMinimal,
} from "@/client"
import { integrationsConnectProvider } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CatalogHeader } from "@/components/catalog/catalog-header"
import { ProviderIcon } from "@/components/icons"
import { MCPIntegrationDialog } from "@/components/integrations/mcp-integration-dialog"
import { OAuthIntegrationDetailsDialog } from "@/components/integrations/oauth-integration-details-dialog"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"
import { useIntegrations, useListMcpIntegrations } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const CREATE_MCP_SERVER_PARAM = "createMcpServer"

type PresetFilter = "all" | "connected" | "workspace"

type McpItem =
  | {
      kind: "platform"
      sortKey: string
      provider: ProviderReadMinimal
    }
  | {
      kind: "workspace"
      sortKey: string
      mcp: MCPIntegrationRead
    }

const PRESET_FILTERS: Array<{ value: PresetFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "connected", label: "Connected" },
  { value: "workspace", label: "Workspace" },
]

export default function McpServersPage() {
  const workspaceId = useWorkspaceId()
  const canRead = useScopeCheck("integration:read")
  const canMutate = useScopeCheck("integration:update") === true

  const { mcpIntegrations, mcpIntegrationsIsLoading, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId, "workspace")
  const { providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)

  const [searchQuery, setSearchQuery] = useState("")
  const [presetFilter, setPresetFilter] = useState<PresetFilter>("all")
  const [createOpen, setCreateOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [activeProvider, setActiveProvider] = useState<{
    providerId: string
    grantType: OAuthGrantType
  } | null>(null)

  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()

  const handleCreateSignalConsumed = useCallback(() => {
    if (!pathname || !searchParams?.get(CREATE_MCP_SERVER_PARAM)) {
      return
    }
    const params = new URLSearchParams(searchParams.toString())
    params.delete(CREATE_MCP_SERVER_PARAM)
    const next = params.toString()
    router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false })
  }, [pathname, router, searchParams])

  useEffect(() => {
    if (!searchParams?.get(CREATE_MCP_SERVER_PARAM)) {
      return
    }
    setCreateOpen(true)
    handleCreateSignalConsumed()
  }, [handleCreateSignalConsumed, searchParams])

  const queryClient = useQueryClient()
  const connectProviderMutation = useMutation({
    mutationFn: async ({ providerId }: { providerId: string }) =>
      await integrationsConnectProvider({ providerId, workspaceId }),
    onSuccess: (result) => {
      window.location.href = result.auth_url
    },
    onError: (error: TracecatApiError) => {
      toast({
        title: "Failed to start OAuth",
        description: String(error.body?.detail ?? error.message),
        variant: "destructive",
      })
    },
  })

  const items = useMemo<McpItem[]>(() => {
    const platformItems: McpItem[] = (providers ?? [])
      .filter((p) => p.id.endsWith("_mcp"))
      .map((provider) => ({
        kind: "platform" as const,
        sortKey: provider.name.toLowerCase(),
        provider,
      }))

    const workspaceItems: McpItem[] = (mcpIntegrations ?? []).map((mcp) => ({
      kind: "workspace" as const,
      sortKey: mcp.name.toLowerCase(),
      mcp,
    }))

    return [...platformItems, ...workspaceItems].sort((a, b) =>
      a.sortKey.localeCompare(b.sortKey)
    )
  }, [mcpIntegrations, providers])

  const filteredItems = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return items.filter((item) => {
      // Preset filter
      if (presetFilter === "workspace" && item.kind !== "workspace") {
        return false
      }
      if (presetFilter === "connected") {
        if (item.kind === "platform") {
          if (item.provider.integration_status !== "connected") {
            return false
          }
        }
        // Workspace items are user-configured, so they always pass "connected".
      }

      // Search
      if (!q) return true
      if (item.kind === "platform") {
        const { name, description, id } = item.provider
        return (
          name.toLowerCase().includes(q) ||
          (description ?? "").toLowerCase().includes(q) ||
          id.toLowerCase().includes(q)
        )
      }
      const { name, description, slug } = item.mcp
      return (
        name.toLowerCase().includes(q) ||
        (description ?? "").toLowerCase().includes(q) ||
        slug.toLowerCase().includes(q)
      )
    })
  }, [items, presetFilter, searchQuery])

  const totalCount = filteredItems.length
  const isLoading = mcpIntegrationsIsLoading || providersIsLoading
  const loadError = mcpIntegrationsError ?? providersError

  if (canRead !== true) {
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

  return (
    <div className="flex h-full flex-col">
      <CatalogHeader
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="Search MCP servers..."
        pillFilters={PRESET_FILTERS}
        activePillFilters={[presetFilter]}
        onPillFilterToggle={(value) => setPresetFilter(value)}
        displayCount={totalCount}
        countLabel={`server${totalCount === 1 ? "" : "s"}`}
      />

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto flex max-w-7xl flex-col gap-6">
          {loadError ? (
            <Alert variant="destructive">
              <AlertTitle>Failed to load MCP servers</AlertTitle>
              <AlertDescription>
                {String(loadError.body?.detail ?? loadError.message)}
              </AlertDescription>
            </Alert>
          ) : null}

          {isLoading ? (
            <div className="flex h-32 items-center justify-center">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : totalCount === 0 ? (
            <Card className="flex flex-col items-center gap-3 p-8 text-center">
              <Sparkles className="size-8 text-muted-foreground" />
              <div>
                <h2 className="text-sm font-semibold">No MCP servers yet</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  Connect Tracecat agents to an MCP server to extend their
                  toolset.
                </p>
              </div>
              {canMutate ? (
                <Button size="sm" onClick={() => setCreateOpen(true)}>
                  <Plus className="mr-1.5 size-4" />
                  Add MCP server
                </Button>
              ) : null}
            </Card>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {filteredItems.map((item) =>
                item.kind === "platform" ? (
                  <PlatformMcpCard
                    key={`platform:${item.provider.id}`}
                    provider={item.provider}
                    canMutate={canMutate}
                    isConnecting={
                      connectProviderMutation.isPending &&
                      connectProviderMutation.variables?.providerId ===
                        item.provider.id
                    }
                    onConnect={() =>
                      connectProviderMutation.mutate({
                        providerId: item.provider.id,
                      })
                    }
                    onManage={() =>
                      setActiveProvider({
                        providerId: item.provider.id,
                        grantType: item.provider.grant_type,
                      })
                    }
                  />
                ) : (
                  <McpCard
                    key={`workspace:${item.mcp.id}`}
                    mcp={item.mcp}
                    canMutate={canMutate}
                    onEdit={() => setEditingId(item.mcp.id)}
                  />
                )
              )}
            </div>
          )}
        </div>
      </div>

      <OAuthIntegrationDetailsDialog
        providerId={activeProvider?.providerId ?? ""}
        grantType={activeProvider?.grantType ?? "authorization_code"}
        open={activeProvider !== null}
        onOpenChange={(next) => {
          if (!next) {
            setActiveProvider(null)
            queryClient.invalidateQueries({
              queryKey: ["providers", workspaceId],
            })
          }
        }}
      />

      <MCPIntegrationDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        hideTrigger
      />

      <MCPIntegrationDialog
        open={editingId !== null}
        onOpenChange={(next) => {
          if (!next) setEditingId(null)
        }}
        mcpIntegrationId={editingId}
        hideTrigger
      />
    </div>
  )
}

interface PlatformMcpCardProps {
  provider: ProviderReadMinimal
  canMutate: boolean
  isConnecting: boolean
  onConnect: () => void
  onManage: () => void
}

function PlatformMcpCard({
  provider,
  canMutate,
  isConnecting,
  onConnect,
  onManage,
}: PlatformMcpCardProps) {
  const connected = provider.integration_status === "connected"
  return (
    <Card className="flex h-full min-h-[120px] flex-col gap-2.5 border bg-card p-4 shadow-none transition-colors hover:border-foreground/30">
      <div className="flex items-start justify-between gap-3">
        <ProviderIcon providerId={provider.id} className="size-9 shrink-0" />
        {canMutate ? (
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-2.5 text-xs font-medium"
            disabled={!connected && (isConnecting || !provider.enabled)}
            onClick={connected ? onManage : onConnect}
          >
            {!connected && isConnecting ? (
              <Loader2 className="mr-1.5 size-3.5 animate-spin" />
            ) : null}
            {connected ? "Manage" : "Connect"}
          </Button>
        ) : null}
      </div>

      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <h3 className="truncate text-sm font-semibold leading-5 text-foreground">
            {provider.name}
          </h3>
          {connected ? (
            <span className="shrink-0 text-xs text-emerald-700">Connected</span>
          ) : null}
        </div>
        <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">
          {provider.description ?? "No description"}
        </p>
      </div>
    </Card>
  )
}

interface McpCardProps {
  mcp: MCPIntegrationRead
  canMutate: boolean
  onEdit: () => void
}

function McpCard({ mcp, canMutate, onEdit }: McpCardProps) {
  const TransportIcon = mcp.server_type === "stdio" ? Terminal : Globe
  const lastUpdated = (() => {
    if (!mcp.updated_at) return null
    try {
      return formatDistanceToNowStrict(new Date(mcp.updated_at), {
        addSuffix: true,
      })
    } catch {
      return null
    }
  })()

  return (
    <Card className="flex h-full min-h-[120px] flex-col gap-2.5 border bg-card p-4 shadow-none transition-colors hover:border-foreground/30">
      <div className="flex items-start justify-between gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md border bg-muted/20">
          <TransportIcon className="size-4 text-muted-foreground" />
        </div>
        {canMutate ? (
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-2.5 text-xs font-medium"
            onClick={onEdit}
          >
            Edit
          </Button>
        ) : null}
      </div>

      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <h3 className="truncate text-sm font-semibold leading-5 text-foreground">
            {mcp.name}
          </h3>
          <Badge variant="outline" className="h-4 px-1.5 text-[10px] uppercase">
            {mcp.server_type}
          </Badge>
        </div>
        <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">
          {mcp.description ?? "No description"}
        </p>
        {lastUpdated ? (
          <p className="text-[11px] leading-5 text-muted-foreground">
            Updated {lastUpdated}
          </p>
        ) : null}
      </div>
    </Card>
  )
}
