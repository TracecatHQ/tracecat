"use client"

import { ExternalLink, Loader2, Zap } from "lucide-react"
import { useCallback, useMemo, useRef } from "react"
import type { OAuthGrantType } from "@/client"
import { ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ProviderConfigForm } from "@/components/provider-config-form"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useIntegrationProvider } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

interface OAuthIntegrationDialogProps {
  providerId: string
  grantType: OAuthGrantType
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function OAuthIntegrationDialog({
  providerId,
  grantType,
  open,
  onOpenChange,
}: OAuthIntegrationDialogProps) {
  const workspaceId = useWorkspaceId()
  const formRef = useRef<HTMLFormElement | null>(null)
  const submitIntentRef = useRef<"connect" | "save" | null>(null)
  const formId = useMemo(
    () => `oauth-config-${providerId}-${grantType}`,
    [providerId, grantType]
  )

  const {
    provider,
    providerIsLoading,
    providerError,
    integration,
    connectProvider,
    connectProviderIsPending,
    testConnection,
    testConnectionIsPending,
  } = useIntegrationProvider({
    providerId,
    workspaceId,
    grantType,
  })

  const requiresConfig = Boolean(provider?.metadata.requires_config)
  const isAuthCodeGrant = provider?.grant_type === "authorization_code"
  const isEnabled = provider?.metadata.enabled ?? true
  const connectLabel = "Connect"
  const ConnectIcon = isAuthCodeGrant ? ExternalLink : Zap
  const isConnectDisabled =
    !isEnabled ||
    providerIsLoading ||
    !provider ||
    connectProviderIsPending ||
    testConnectionIsPending

  const handleSubmitSuccess = useCallback(async () => {
    const intent = submitIntentRef.current
    submitIntentRef.current = null

    if (intent !== "connect") {
      return
    }

    if (isAuthCodeGrant) {
      await connectProvider(providerId)
      return
    }

    const result = await testConnection(providerId)
    if (result?.success) {
      onOpenChange(false)
    }
  }, [
    connectProvider,
    isAuthCodeGrant,
    onOpenChange,
    providerId,
    testConnection,
  ])

  const handleConnectClick = useCallback(() => {
    if (!requiresConfig) {
      return
    }
    submitIntentRef.current = "connect"
  }, [requiresConfig])

  const handleDialogOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen && requiresConfig && formRef.current) {
        submitIntentRef.current = "save"
        formRef.current.requestSubmit()
      }
      onOpenChange(nextOpen)
    },
    [onOpenChange, requiresConfig]
  )

  if (!open) {
    return null
  }

  return (
    <Dialog open={open} onOpenChange={handleDialogOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="flex flex-col gap-3 p-8 pb-10 text-left">
          <div className="flex items-start gap-4">
            <ProviderIcon providerId={providerId} className="size-10" />
            <div className="space-y-1">
              <DialogTitle>{provider?.metadata.name}</DialogTitle>
              <DialogDescription>
                {provider?.metadata.description ||
                  "Configure the provider credentials and connect."}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>
        <div className="flex-1 overflow-y-auto px-8 pb-8">
          {providerIsLoading && <CenteredSpinner />}
          {providerError && (
            <div className="text-sm text-destructive">
              Failed to load provider details.
            </div>
          )}
          {!providerIsLoading && provider && (
            <div className="space-y-6">
              <ProviderConfigForm
                provider={provider}
                onSuccess={handleSubmitSuccess}
                formId={formId}
                formRef={formRef}
                hideActions
              />
              {integration?.status === "configured" && (
                <p className="text-xs text-muted-foreground">
                  Saved configuration will stay in place even if the connection
                  is not completed.
                </p>
              )}
            </div>
          )}
        </div>
        <DialogFooter className="sticky bottom-0 border-t bg-background px-8 py-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => handleDialogOpenChange(false)}
            disabled={connectProviderIsPending || testConnectionIsPending}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            form={formId}
            onClick={(event) => {
              if (connectProviderIsPending || testConnectionIsPending) {
                event.preventDefault()
                return
              }
              handleConnectClick()
            }}
            disabled={isConnectDisabled}
            className={cn("gap-2")}
          >
            {(connectProviderIsPending || testConnectionIsPending) && (
              <Loader2 className="size-4 animate-spin" />
            )}
            {ConnectIcon && <ConnectIcon className="size-4" />}
            {connectLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
