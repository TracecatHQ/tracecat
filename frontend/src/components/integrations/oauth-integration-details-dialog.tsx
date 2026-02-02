"use client"

import { Loader2 } from "lucide-react"
import { useMemo } from "react"
import type { OAuthGrantType } from "@/client"
import { ProviderIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useIntegrationProvider } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface OAuthIntegrationDetailsDialogProps {
  providerId: string
  grantType: OAuthGrantType
  open: boolean
  onOpenChange: (open: boolean) => void
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
}: OAuthIntegrationDetailsDialogProps) {
  const workspaceId = useWorkspaceId()

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

  const isServiceAccountProvider = provider?.metadata.id === "google"
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

  if (!open) {
    return null
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl gap-0 p-0 overflow-hidden max-h-[85vh] flex flex-col">
        <DialogHeader className="p-6 pb-4">
          <div className="flex items-start gap-4">
            <ProviderIcon providerId={providerId} className="size-10" />
            <div className="space-y-1 text-left">
              <DialogTitle>{provider?.metadata.name || providerId}</DialogTitle>
              <DialogDescription>
                Connection details for this OAuth integration.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <ScrollArea className="flex-1 px-6 pb-6">
          {(providerIsLoading || integrationIsLoading) && (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          )}

          {(providerError || integrationError) && (
            <div className="text-sm text-destructive">
              Failed to load integration details.
            </div>
          )}

          {!providerIsLoading && !integrationIsLoading && provider && integration && (
            <div className="space-y-8">
              <div className="space-y-6">
                <h3 className="font-medium">Configuration</h3>
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
                      <span className="font-medium text-muted-foreground">Expires</span>
                      <span className="text-xs text-foreground">{tokenExpires}</span>
                    </div>
                  )}
                </div>
              </div>

              <div className="space-y-6">
                <h3 className="font-medium">Scopes</h3>
                <div className="space-y-4">
                  {hasScopes ? (
                    <>
                      {requestedScopes.length > 0 && (
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-muted-foreground">
                              Requested scopes
                            </span>
                          </div>
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
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-muted-foreground">
                              Granted scopes
                            </span>
                          </div>
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
              </div>
            </div>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
