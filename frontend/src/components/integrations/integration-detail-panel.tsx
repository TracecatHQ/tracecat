"use client"

import { formatDistanceToNowStrict } from "date-fns"
import { Plug, Trash2, Wrench } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type {
  CatalogAuthOption,
  CatalogConnectionRead,
  CatalogIntegrationDetail,
} from "@/client"
import { ProviderIcon } from "@/components/icons"
import { ConfigureDialog } from "@/components/integrations/configure-dialog"
import { ConnectDialog } from "@/components/integrations/connect-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useDeleteConnection,
  useIntegrationDetail,
} from "@/lib/hooks/integrations-catalog"

interface IntegrationDetailPanelProps {
  workspaceId: string
  integrationId: string | null
  onClose: () => void
}

export function IntegrationDetailPanel({
  workspaceId,
  integrationId,
  onClose,
}: IntegrationDetailPanelProps) {
  const open = Boolean(integrationId)
  const { integration, integrationIsLoading } = useIntegrationDetail(
    workspaceId,
    integrationId
  )
  const [lastIntegration, setLastIntegration] =
    useState<CatalogIntegrationDetail | null>(null)
  const renderedIntegration = integration ?? (open ? null : lastIntegration)

  useEffect(() => {
    if (integration) {
      setLastIntegration(integration)
    }
  }, [integration])

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent className="min-h-[220px] w-[calc(100vw-2rem)] max-w-2xl gap-4 p-0 data-[state=closed]:zoom-out-100 data-[state=open]:zoom-in-100 sm:w-full">
        {integrationIsLoading || !renderedIntegration ? (
          <IntegrationDetailSkeleton />
        ) : (
          <IntegrationDetailContent
            key={renderedIntegration.id}
            integration={renderedIntegration}
            workspaceId={workspaceId}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function IntegrationDetailSkeleton() {
  return (
    <>
      <DialogHeader className="px-6 pt-6">
        <DialogTitle className="sr-only">Loading integration</DialogTitle>
        <div className="flex items-start gap-3">
          <Skeleton className="size-11 shrink-0" />
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-3 w-72 max-w-full" />
          </div>
          <Skeleton className="h-7 w-24 shrink-0" />
        </div>
      </DialogHeader>

      <div className="flex max-h-[60vh] min-h-[116px] flex-col gap-3 overflow-y-auto px-6 pb-6">
        <div className="flex items-center justify-between">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-7 w-28" />
        </div>
        <Skeleton className="h-12 w-full" />
      </div>
    </>
  )
}

interface IntegrationDetailContentProps {
  integration: CatalogIntegrationDetail
  workspaceId: string
}

function isConnectableOption(option: CatalogAuthOption): boolean {
  return (
    option.enabled !== false &&
    (option.auth_method === "oauth_auth_code" ||
      option.auth_method === "static_kv")
  )
}

function isConfigurableOption(option: CatalogAuthOption): boolean {
  return Boolean(
    option.enabled !== false &&
      option.provider_id &&
      option.grant_type &&
      option.requires_config
  )
}

function authMethodLabel(
  authMethod: CatalogConnectionRead["auth_method"]
): string {
  switch (authMethod) {
    case "oauth_auth_code":
      return "OAuth"
    case "oauth_client_credentials":
      return "Client credentials"
    case "service_account":
      return "Service account"
    case "static_kv":
      return "Static credentials"
    default:
      return authMethod
  }
}

function IntegrationDetailContent({
  integration,
  workspaceId,
}: IntegrationDetailContentProps) {
  const [connectOpen, setConnectOpen] = useState(false)
  const [configureOpen, setConfigureOpen] = useState(false)
  const [configureOption, setConfigureOption] =
    useState<CatalogAuthOption | null>(null)
  const isWorkspaceBuilt = integration.source === "workspace"
  const authOptions = integration.auth_options ?? []
  const connectableOptions = authOptions.filter(isConnectableOption)
  const configurableOptions = authOptions.filter(isConfigurableOption)
  const missingConfigOption =
    configurableOptions.find((option) => option.status === "not_configured") ??
    null
  const openConfigure = (option?: CatalogAuthOption | null) => {
    setConfigureOption(
      option ?? missingConfigOption ?? configurableOptions[0] ?? null
    )
    setConfigureOpen(true)
  }

  return (
    <>
      <DialogHeader className="px-6 pt-6">
        <div className="flex items-start gap-3">
          <ProviderIcon
            providerId={integration.namespace}
            className="size-11 shrink-0"
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <DialogTitle className="truncate text-lg leading-tight">
                {integration.display_name}
              </DialogTitle>
              {isWorkspaceBuilt ? (
                <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                  Built by me
                </Badge>
              ) : null}
            </div>
            <DialogDescription className="mt-1 text-xs">
              {integration.description ?? "No description"}
            </DialogDescription>
          </div>
          {configurableOptions.length > 0 ? (
            <Button
              size="sm"
              variant="outline"
              className="h-7 shrink-0 gap-1.5 px-2.5 text-xs"
              onClick={() => openConfigure()}
            >
              <Wrench className="size-3.5" />
              Configure
            </Button>
          ) : null}
        </div>
      </DialogHeader>

      <div className="max-h-[60vh] min-h-[116px] space-y-5 overflow-y-auto px-6 pb-6">
        <ConnectionsSection
          workspaceId={workspaceId}
          integrationId={integration.id}
          connections={integration.connections ?? []}
          canAddConnection={connectableOptions.length > 0}
          canConfigure={configurableOptions.length > 0}
          onAddConnection={() => setConnectOpen(true)}
        />
      </div>

      <ConnectDialog
        open={connectOpen}
        onOpenChange={setConnectOpen}
        workspaceId={workspaceId}
        integrationId={integration.id}
        namespace={integration.namespace}
        displayName={integration.display_name}
        authOptions={authOptions}
        onConfigure={(option) => {
          setConnectOpen(false)
          openConfigure(option)
        }}
      />

      <ConfigureDialog
        open={configureOpen}
        onOpenChange={setConfigureOpen}
        workspaceId={workspaceId}
        integrationId={integration.id}
        displayName={integration.display_name}
        authOptions={authOptions}
        defaultAuthOption={configureOption}
      />
    </>
  )
}

function ConnectionsSection({
  workspaceId,
  integrationId,
  connections,
  canAddConnection,
  canConfigure,
  onAddConnection,
}: {
  workspaceId: string
  integrationId: string
  connections: CatalogConnectionRead[]
  canAddConnection: boolean
  canConfigure: boolean
  onAddConnection: () => void
}) {
  const { deleteConnection, deleteConnectionIsPending } = useDeleteConnection(
    workspaceId,
    integrationId
  )

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Plug className="size-4 text-muted-foreground" />
          <h3 className="text-sm font-semibold">Connections</h3>
          <Badge variant="outline" className="h-4 px-1.5 text-[10px]">
            {connections.length}
          </Badge>
        </div>
        {canAddConnection ? (
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-2.5 text-xs"
            onClick={onAddConnection}
          >
            Add connection
          </Button>
        ) : null}
      </div>
      {connections.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {canAddConnection
            ? "No connections yet. Add a connection to authenticate this integration."
            : canConfigure
              ? "Use Configure to finish credential setup for this integration."
              : "No separate connection is required for this integration."}
        </p>
      ) : (
        <ul className="space-y-2">
          {connections.map((connection) => (
            <ConnectionRow
              key={connection.id}
              connection={connection}
              onDelete={() => deleteConnection(connection.id)}
              isDeleting={deleteConnectionIsPending}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

function ConnectionRow({
  connection,
  onDelete,
  isDeleting,
}: {
  connection: CatalogConnectionRead
  onDelete: () => void
  isDeleting: boolean
}) {
  const lastUpdated = useMemo(() => {
    if (!connection.updated_at) return null
    try {
      return formatDistanceToNowStrict(new Date(connection.updated_at), {
        addSuffix: true,
      })
    } catch {
      return null
    }
  }, [connection.updated_at])

  return (
    <li className="flex items-center justify-between gap-3 rounded-md border bg-muted/20 px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">
            {connection.label}
          </span>
          <Badge variant="outline" className="h-4 px-1.5 text-[10px]">
            {authMethodLabel(connection.auth_method)}
          </Badge>
          {connection.is_expired ? (
            <Badge
              variant="outline"
              className="h-4 gap-1 border-destructive/40 bg-destructive/10 px-1.5 text-[10px] text-destructive"
            >
              Expired
            </Badge>
          ) : null}
        </div>
        {lastUpdated ? (
          <p className="text-[11px] text-muted-foreground">
            Updated {lastUpdated}
          </p>
        ) : null}
      </div>
      <Button
        size="icon"
        variant="ghost"
        className="size-7 shrink-0 text-muted-foreground hover:text-destructive"
        disabled={isDeleting}
        onClick={onDelete}
        aria-label={`Remove connection ${connection.label}`}
      >
        <Trash2 className="size-4" />
      </Button>
    </li>
  )
}
