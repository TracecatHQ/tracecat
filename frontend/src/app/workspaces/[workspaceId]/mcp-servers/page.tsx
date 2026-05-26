"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { formatDistanceToNowStrict } from "date-fns"
import {
  Cable,
  Globe,
  Loader2,
  Lock,
  Plus,
  Settings,
  Sparkles,
  Terminal,
  Trash2,
} from "lucide-react"
import { useMemo, useState } from "react"
import type {
  MCPIntegrationRead,
  OAuthGrantType,
  ProviderReadMinimal,
} from "@/client"
import { integrationsConnectProvider } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ProviderIcon } from "@/components/icons"
import { MCPIntegrationDialog } from "@/components/integrations/mcp-integration-dialog"
import { OAuthIntegrationDetailsDialog } from "@/components/integrations/oauth-integration-details-dialog"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"
import {
  useDeleteMcpIntegration,
  useIntegrations,
  useListMcpIntegrations,
} from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function McpServersPage() {
  const workspaceId = useWorkspaceId()
  const canRead = useScopeCheck("integration:read")
  const canMutate = useScopeCheck("integration:update") === true

  const { mcpIntegrations, mcpIntegrationsIsLoading, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId)
  const { providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)
  const { deleteMcpIntegration, deleteMcpIntegrationIsPending } =
    useDeleteMcpIntegration(workspaceId)

  const [searchQuery, setSearchQuery] = useState("")
  const [createOpen, setCreateOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [activeProvider, setActiveProvider] = useState<{
    providerId: string
    grantType: OAuthGrantType
  } | null>(null)

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

  const platformMcpProviders = useMemo<ProviderReadMinimal[]>(() => {
    if (!providers) return []
    return providers.filter((p) => p.id.endsWith("_mcp"))
  }, [providers])

  const filteredWorkspaceServers = useMemo(() => {
    if (!mcpIntegrations) return []
    const q = searchQuery.trim().toLowerCase()
    return mcpIntegrations
      .filter((mcp) => {
        if (!q) return true
        return (
          mcp.name.toLowerCase().includes(q) ||
          (mcp.description ?? "").toLowerCase().includes(q) ||
          mcp.slug.toLowerCase().includes(q)
        )
      })
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [mcpIntegrations, searchQuery])

  const filteredPlatformProviders = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return platformMcpProviders
      .filter((p) => {
        if (!q) return true
        return (
          p.name.toLowerCase().includes(q) ||
          (p.description ?? "").toLowerCase().includes(q) ||
          p.id.toLowerCase().includes(q)
        )
      })
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [platformMcpProviders, searchQuery])

  const totalCount =
    filteredWorkspaceServers.length + filteredPlatformProviders.length
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

  const pendingDelete = pendingDeleteId
    ? (mcpIntegrations?.find((mcp) => mcp.id === pendingDeleteId) ?? null)
    : null

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b px-6 py-3">
        <Input
          placeholder="Search MCP servers..."
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          className="h-8 max-w-xs"
        />
        <span className="text-xs text-muted-foreground">
          {totalCount} server{totalCount === 1 ? "" : "s"}
        </span>
        {canMutate ? (
          <Button
            size="sm"
            className="ml-auto h-8 gap-1.5"
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="size-4" />
            Add MCP server
          </Button>
        ) : null}
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto flex max-w-5xl flex-col gap-6">
          <p className="text-sm text-muted-foreground">
            MCP servers that Tracecat agents can call. Separate from
            integrations that back actions.
          </p>

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
            <>
              {filteredPlatformProviders.length > 0 ? (
                <section className="space-y-2">
                  <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Platform-shipped
                  </h2>
                  <ul className="space-y-2">
                    {filteredPlatformProviders.map((provider) => (
                      <PlatformMcpRow
                        key={provider.id}
                        provider={provider}
                        canMutate={canMutate}
                        isConnecting={
                          connectProviderMutation.isPending &&
                          connectProviderMutation.variables?.providerId ===
                            provider.id
                        }
                        onConnect={() =>
                          connectProviderMutation.mutate({
                            providerId: provider.id,
                          })
                        }
                        onManage={() =>
                          setActiveProvider({
                            providerId: provider.id,
                            grantType: provider.grant_type,
                          })
                        }
                      />
                    ))}
                  </ul>
                </section>
              ) : null}

              {filteredWorkspaceServers.length > 0 ? (
                <section className="space-y-2">
                  <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Workspace-authored
                  </h2>
                  <ul className="space-y-2">
                    {filteredWorkspaceServers.map((mcp) => (
                      <McpRow
                        key={mcp.id}
                        mcp={mcp}
                        canMutate={canMutate}
                        onEdit={() => setEditingId(mcp.id)}
                        onDelete={() => setPendingDeleteId(mcp.id)}
                      />
                    ))}
                  </ul>
                </section>
              ) : null}
            </>
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

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(next) => {
          if (!next) setPendingDeleteId(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove MCP server?</AlertDialogTitle>
            <AlertDialogDescription>
              Agents will no longer be able to call{" "}
              <span className="font-medium">{pendingDelete?.name}</span>.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleteMcpIntegrationIsPending}
              onClick={async () => {
                if (!pendingDeleteId) return
                await deleteMcpIntegration(pendingDeleteId)
                setPendingDeleteId(null)
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

interface PlatformMcpRowProps {
  provider: ProviderReadMinimal
  canMutate: boolean
  isConnecting: boolean
  onConnect: () => void
  onManage: () => void
}

function PlatformMcpRow({
  provider,
  canMutate,
  isConnecting,
  onConnect,
  onManage,
}: PlatformMcpRowProps) {
  const connected = provider.integration_status === "connected"
  return (
    <li>
      <Card className="flex items-center gap-3 border bg-card p-3 shadow-none">
        <ProviderIcon providerId={provider.id} className="size-9 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{provider.name}</h3>
            <Badge
              variant="outline"
              className="h-4 px-1.5 text-[10px] uppercase"
            >
              Platform
            </Badge>
            {connected ? (
              <Badge
                variant="outline"
                className="h-4 px-1.5 text-[10px] border-emerald-400/50 bg-emerald-500/10 text-emerald-700"
              >
                Connected
              </Badge>
            ) : null}
          </div>
          {provider.description ? (
            <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
              {provider.description}
            </p>
          ) : null}
        </div>
        {canMutate ? (
          <div className="flex shrink-0 gap-1">
            {connected ? (
              <Button
                size="sm"
                variant="outline"
                className="h-7 gap-1.5 px-2.5 text-xs"
                onClick={onManage}
              >
                <Settings className="size-3.5" />
                Manage
              </Button>
            ) : (
              <Button
                size="sm"
                className="h-7 gap-1.5 px-2.5 text-xs"
                disabled={isConnecting || !provider.enabled}
                onClick={onConnect}
              >
                {isConnecting ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Lock className="size-3.5" />
                )}
                Connect
              </Button>
            )}
          </div>
        ) : null}
      </Card>
    </li>
  )
}

interface McpRowProps {
  mcp: MCPIntegrationRead
  canMutate: boolean
  onEdit: () => void
  onDelete: () => void
}

function McpRow({ mcp, canMutate, onEdit, onDelete }: McpRowProps) {
  const TransportIcon = mcp.server_type === "stdio" ? Terminal : Globe
  const target =
    mcp.server_type === "stdio" ? mcp.stdio_command : mcp.server_uri
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
    <li>
      <Card className="flex items-center gap-3 border bg-card p-3 shadow-none">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md border bg-muted/20">
          <TransportIcon className="size-4 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{mcp.name}</h3>
            <Badge
              variant="outline"
              className="h-4 px-1.5 text-[10px] uppercase"
            >
              {mcp.server_type}
            </Badge>
            <Badge
              variant="outline"
              className="h-4 px-1.5 text-[10px] uppercase"
            >
              {mcp.auth_type}
            </Badge>
          </div>
          {target ? (
            <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
              {target}
            </p>
          ) : null}
          {mcp.description ? (
            <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
              {mcp.description}
            </p>
          ) : null}
          {lastUpdated ? (
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Updated {lastUpdated}
            </p>
          ) : null}
        </div>
        {canMutate ? (
          <div className="flex shrink-0 gap-1">
            <Button
              size="sm"
              variant="outline"
              className="h-7 gap-1.5 px-2.5 text-xs"
              onClick={onEdit}
            >
              <Cable className="size-3.5" />
              Edit
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="size-7 text-muted-foreground hover:text-destructive"
              onClick={onDelete}
              aria-label={`Remove MCP server ${mcp.name}`}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ) : null}
      </Card>
    </li>
  )
}
