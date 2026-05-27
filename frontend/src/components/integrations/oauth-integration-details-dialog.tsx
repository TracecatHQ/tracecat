"use client"

import {
  CheckCircle2,
  Link2,
  Loader2,
  MoreHorizontal,
  PlayCircle,
  Trash2,
} from "lucide-react"
import { useMemo, useState } from "react"
import type { OAuthGrantType } from "@/client"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"
import { ProviderIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useConnectProvider,
  useDisconnectProvider,
  useTestProvider,
} from "@/hooks/use-integration-actions"
import { useIntegrationProvider } from "@/lib/hooks"
import { formatRelative } from "@/lib/time"
import { useWorkspaceId } from "@/providers/workspace-id"

interface OAuthIntegrationDetailsDialogProps {
  providerId: string
  grantType: OAuthGrantType
  open: boolean
  onOpenChange: (open: boolean) => void
  canUpdate?: boolean
}

function maskValue(value?: string | null) {
  if (!value) return "Not configured"
  const trimmed = value.trim()
  if (trimmed.length <= 6) return trimmed
  const suffix = trimmed.length > 10 ? trimmed.slice(-4) : ""
  return `${trimmed.slice(0, 6)}****${suffix}`
}

export function OAuthIntegrationDetailsDialog({
  providerId,
  grantType,
  open,
  onOpenChange,
  canUpdate = false,
}: OAuthIntegrationDetailsDialogProps) {
  const workspaceId = useWorkspaceId()
  const [confirmDisconnectOpen, setConfirmDisconnectOpen] = useState(false)

  const {
    provider,
    providerIsLoading,
    providerError,
    integration,
    integrationIsLoading,
    integrationError,
  } = useIntegrationProvider({
    providerId,
    workspaceId,
    grantType,
  })

  const reauthorizeMutation = useConnectProvider(workspaceId)
  const testMutation = useTestProvider(workspaceId)
  const disconnectMutation = useDisconnectProvider(workspaceId)

  const providerName = provider?.metadata.name || providerId
  const isConnected = integration?.status === "connected"
  const isExpired = integration?.is_expired ?? false

  const serviceAccountProviders = ["google", "google_sheets", "google_docs"]
  const isServiceAccountProvider = serviceAccountProviders.includes(
    provider?.metadata.id ?? ""
  )
  const clientIdLabel = isServiceAccountProvider
    ? "Service account email"
    : "Client ID"

  const requestedScopes = integration?.requested_scopes ?? []
  const grantedScopes = integration?.granted_scopes ?? []
  const hasScopes = requestedScopes.length > 0 || grantedScopes.length > 0

  const tokenExpires = useMemo(() => {
    if (!integration?.expires_at) return null
    try {
      return new Date(integration.expires_at).toLocaleString()
    } catch {
      return integration.expires_at
    }
  }, [integration?.expires_at])

  const lastUpdatedRelative = formatRelative(integration?.updated_at)

  if (!open) {
    return null
  }

  const supportsReauthorize = grantType === "authorization_code"
  const supportsTest = grantType === "client_credentials"
  const anyActionPending =
    reauthorizeMutation.isPending ||
    testMutation.isPending ||
    disconnectMutation.isPending

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl gap-0 p-0 overflow-hidden max-h-[85vh] min-h-[520px] flex flex-col">
        <DialogHeader className="border-b p-6">
          <div className="flex items-start gap-4">
            <ProviderIcon providerId={providerId} className="size-10" />
            <div className="min-w-0 flex-1 space-y-1 text-left">
              <DialogTitle className="text-lg font-semibold">
                {provider?.metadata.name ? (
                  providerName
                ) : providerIsLoading ? (
                  <Skeleton className="h-5 w-32" />
                ) : (
                  providerName
                )}
              </DialogTitle>
              <DialogDescription className="text-sm" asChild>
                {provider?.metadata ? (
                  <span>
                    {provider.metadata.description ??
                      "Connection details for this integration."}
                  </span>
                ) : providerIsLoading ? (
                  <span className="block space-y-1.5 pt-1">
                    <Skeleton className="h-3 w-3/4" />
                    <Skeleton className="h-3 w-1/2" />
                  </span>
                ) : (
                  <span>Connection details for this integration.</span>
                )}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <ScrollArea className="flex-1">
          {(providerIsLoading || integrationIsLoading) && <DetailsSkeleton />}

          {!providerIsLoading &&
            !integrationIsLoading &&
            (providerError || integrationError) && (
              <div className="px-6 py-4 text-sm text-destructive">
                Failed to load integration details.
              </div>
            )}

          {!providerIsLoading &&
            !integrationIsLoading &&
            provider &&
            integration && (
              <div className="flex flex-col">
                <section className="space-y-3 px-6 py-5">
                  <h3 className="text-sm font-semibold">Connection</h3>
                  {isConnected ? (
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex min-w-0 flex-1 items-center gap-3">
                        <span
                          className={
                            isExpired
                              ? "flex size-7 shrink-0 items-center justify-center rounded-full bg-amber-100 text-amber-700"
                              : "flex size-7 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-700"
                          }
                        >
                          <CheckCircle2 className="size-4" />
                        </span>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <p className="truncate text-sm font-medium text-foreground">
                              {providerName}
                            </p>
                            {isExpired ? (
                              <Badge
                                variant="outline"
                                className="h-4 border-amber-300 bg-amber-50 px-1.5 text-[10px] uppercase text-amber-700"
                              >
                                Expired
                              </Badge>
                            ) : null}
                          </div>
                          <p className="text-xs text-muted-foreground">
                            {lastUpdatedRelative
                              ? `Last updated ${lastUpdatedRelative}`
                              : "Connected"}
                          </p>
                        </div>
                      </div>
                      {canUpdate ? (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-8 shrink-0 text-muted-foreground"
                              disabled={anyActionPending}
                              aria-label="Connection actions"
                            >
                              {anyActionPending ? (
                                <Loader2 className="size-4 animate-spin" />
                              ) : (
                                <MoreHorizontal className="size-4" />
                              )}
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-44">
                            {supportsReauthorize ? (
                              <DropdownMenuItem
                                onClick={() =>
                                  reauthorizeMutation.mutate({ providerId })
                                }
                                disabled={reauthorizeMutation.isPending}
                              >
                                <Link2 className="mr-2 size-4 text-muted-foreground" />
                                Reauthorize
                              </DropdownMenuItem>
                            ) : null}
                            {supportsTest ? (
                              <DropdownMenuItem
                                onClick={() =>
                                  testMutation.mutate({ providerId, grantType })
                                }
                                disabled={testMutation.isPending}
                              >
                                <PlayCircle className="mr-2 size-4 text-muted-foreground" />
                                Test
                              </DropdownMenuItem>
                            ) : null}
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              className="text-destructive focus:bg-destructive/10 focus:text-destructive"
                              onSelect={(event) => {
                                event.preventDefault()
                                setConfirmDisconnectOpen(true)
                              }}
                            >
                              <Trash2 className="mr-2 size-4" />
                              Disconnect
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      ) : null}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Not connected yet.
                    </p>
                  )}
                </section>

                <section className="space-y-4 border-t px-6 py-5">
                  <h3 className="text-sm font-semibold">Configuration</h3>
                  <div className="grid grid-cols-2 gap-x-12 gap-y-4 text-sm">
                    <div className="flex flex-col gap-1.5">
                      <span className="font-medium text-muted-foreground">
                        {clientIdLabel}
                      </span>
                      <span className="font-mono text-xs text-foreground break-all">
                        {maskValue(integration.client_id)}
                      </span>
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <span className="font-medium text-muted-foreground">
                        Client secret
                      </span>
                      <span className="text-xs text-foreground">
                        {integration.status !== "not_configured"
                          ? "Configured"
                          : "Not configured"}
                      </span>
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <span className="font-medium text-muted-foreground">
                        Authorization endpoint
                      </span>
                      <span className="text-xs text-foreground break-all">
                        {integration.authorization_endpoint ||
                          provider.default_authorization_endpoint ||
                          "Not configured"}
                      </span>
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <span className="font-medium text-muted-foreground">
                        Token endpoint
                      </span>
                      <span className="text-xs text-foreground break-all">
                        {integration.token_endpoint ||
                          provider.default_token_endpoint ||
                          "Not configured"}
                      </span>
                    </div>
                    {tokenExpires && (
                      <div className="flex flex-col gap-1.5">
                        <span className="font-medium text-muted-foreground">
                          Expires
                        </span>
                        <span className="text-xs text-foreground">
                          {tokenExpires}
                        </span>
                      </div>
                    )}
                  </div>
                </section>

                <section className="space-y-4 border-t px-6 py-5">
                  <h3 className="text-sm font-semibold">Scopes</h3>
                  <div className="space-y-4">
                    {hasScopes ? (
                      <>
                        {requestedScopes.length > 0 && (
                          <div className="flex flex-col gap-2">
                            <span className="text-xs font-medium text-muted-foreground">
                              Requested
                            </span>
                            <div className="flex flex-wrap gap-1">
                              {requestedScopes.map((scope) => (
                                <Badge
                                  key={`requested-${scope}`}
                                  variant="secondary"
                                  className="text-xs"
                                >
                                  {scope}
                                </Badge>
                              ))}
                            </div>
                          </div>
                        )}
                        {grantedScopes.length > 0 && (
                          <div className="flex flex-col gap-2">
                            <span className="text-xs font-medium text-muted-foreground">
                              Granted
                            </span>
                            <div className="flex flex-wrap gap-1">
                              {grantedScopes.map((scope) => (
                                <Badge
                                  key={`granted-${scope}`}
                                  variant="outline"
                                  className="text-xs"
                                >
                                  {scope}
                                </Badge>
                              ))}
                            </div>
                          </div>
                        )}
                      </>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        No scopes configured for this integration.
                      </p>
                    )}
                  </div>
                </section>
              </div>
            )}
        </ScrollArea>

        <ConfirmDestructiveDialog
          open={confirmDisconnectOpen}
          onOpenChange={setConfirmDisconnectOpen}
          confirmPhrase={providerName}
          title="Disconnect integration"
          description={
            <>
              Are you sure you want to disconnect from{" "}
              <span className="font-medium">{providerName}</span>?
            </>
          }
          confirmLabel="Disconnect"
          isPending={disconnectMutation.isPending}
          onConfirm={async () => {
            await disconnectMutation.mutateAsync({ providerId, grantType })
            setConfirmDisconnectOpen(false)
            onOpenChange(false)
          }}
        />
      </DialogContent>
    </Dialog>
  )
}

function DetailsSkeleton() {
  return (
    <div className="flex flex-col">
      <section className="space-y-3 px-6 py-5">
        <Skeleton className="h-4 w-24" />
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <Skeleton className="size-7 shrink-0 rounded-full" />
            <div className="min-w-0 space-y-1.5">
              <Skeleton className="h-3.5 w-32" />
              <Skeleton className="h-3 w-40" />
            </div>
          </div>
          <Skeleton className="size-8 shrink-0 rounded-md" />
        </div>
      </section>

      <section className="space-y-4 border-t px-6 py-5">
        <Skeleton className="h-4 w-24" />
        <div className="grid grid-cols-2 gap-x-12 gap-y-4">
          {[
            "client-id",
            "client-secret",
            "auth-endpoint",
            "token-endpoint",
          ].map((key) => (
            <div key={key} className="flex flex-col gap-2">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-3 w-40" />
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-3 border-t px-6 py-5">
        <Skeleton className="h-4 w-16" />
        <div className="flex flex-wrap gap-1.5">
          {[14, 18, 12, 16, 20].map((width) => (
            <Skeleton
              key={width}
              className="h-5 rounded-md"
              style={{ width: `${width * 4}px` }}
            />
          ))}
        </div>
      </section>
    </div>
  )
}
