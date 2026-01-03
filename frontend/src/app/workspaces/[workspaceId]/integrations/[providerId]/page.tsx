"use client"

import {
  AlertCircle,
  ChevronLeft,
  ExternalLink,
  Key,
  LayoutListIcon,
  Loader2,
  Settings,
  Trash,
  Unplug,
  User,
  Zap,
} from "lucide-react"
import Link from "next/link"
import {
  notFound,
  useParams,
  useRouter,
  useSearchParams,
} from "next/navigation"
import { useCallback, useState } from "react"
import type { OAuthGrantType, ProviderRead } from "@/client"
import { ProviderIcon } from "@/components/icons"
import { statusStyles } from "@/components/integrations/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ProviderConfigForm } from "@/components/provider-config-form"
import { RedirectUriDisplay } from "@/components/redirect-uri-display"
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useIntegrationProvider } from "@/lib/hooks"
import { isCustomProvider, isMCPProvider } from "@/lib/providers"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function ProviderDetailPage() {
  const searchParams = useSearchParams()
  const params = useParams()
  const workspaceId = useWorkspaceId()

  if (!params) {
    return <div>Error: Invalid parameters</div>
  }

  const providerId = params.providerId as string
  const grantType = searchParams?.get("grant_type") as OAuthGrantType | null

  const { provider, providerIsLoading, providerError } = useIntegrationProvider(
    {
      providerId,
      workspaceId,
      grantType: grantType || undefined,
    }
  )

  if (!grantType) {
    notFound()
  }

  if (providerIsLoading) {
    return <CenteredSpinner />
  }

  if (providerError) {
    return <div>Error: {providerError.message}</div>
  }
  if (!provider) {
    return (
      <div className="container mx-auto max-w-4xl p-6">
        <div className="flex flex-col items-center justify-center space-y-4 py-12">
          <AlertCircle className="size-12 text-muted-foreground" />
          <h2 className="text-xl font-semibold">Provider not found</h2>
          <div className="text-muted-foreground">
            The requested integration provider could not be found or metadata
            could not be loaded.
          </div>
          <Link href={`/workspaces/${workspaceId}/integrations`}>
            <Button variant="outline" className="mt-2">
              <ChevronLeft className="mr-2 size-4" />
              Back to Integrations
            </Button>
          </Link>
        </div>
      </div>
    )
  }
  return <ProviderDetailContent provider={provider} />
}

type ProviderDetailTab = "overview" | "configuration"

function ProviderDetailContent({ provider }: { provider: ProviderRead }) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const searchParams = useSearchParams()
  const [_showConnectPrompt, setShowConnectPrompt] = useState(false)
  const providerId = provider.metadata.id
  const isMCP = isMCPProvider(provider)
  const isCustom = isCustomProvider(provider)
  const requiresConfiguration = Boolean(provider.metadata.requires_config)
  const isAuthCodeGrant = provider.grant_type === "authorization_code"
  const isSelfConfiguringMCP = isMCP && !requiresConfiguration

  // Get active tab from URL query params, default to "overview" or "configuration"
  // For custom providers, always use "configuration" since there's no overview tab
  // For MCP providers, always use "overview" since there's no configuration tab
  const activeTab = (
    searchParams &&
    ["overview", "configuration"].includes(searchParams.get("tab") || "")
      ? (() => {
          const requestedTab = searchParams.get("tab") ?? "overview"
          // Force configuration tab for custom providers
          if (isCustom) {
            return "configuration"
          }
          // Don't allow configuration tab for self-configured MCP providers
          if (isSelfConfiguringMCP && requestedTab === "configuration") {
            return "overview"
          }
          return requestedTab
        })()
      : isCustom
        ? "configuration"
        : "overview"
  ) as ProviderDetailTab

  // Function to handle tab changes and update URL
  const handleTabChange = useCallback(
    (tab: string) => {
      // Force configuration tab for custom providers
      const effectiveTab = isCustom ? "configuration" : tab
      const params = new URLSearchParams(searchParams?.toString() || "")
      params.set("tab", effectiveTab)
      router.push(
        `/workspaces/${workspaceId}/integrations/${providerId}?${params.toString()}`
      )
    },
    [router, workspaceId, providerId, searchParams, isCustom]
  )

  // Whether there's a connected integration
  const {
    integration,
    connectProvider,
    connectProviderIsPending,
    disconnectProvider,
    disconnectProviderIsPending,
    deleteIntegration,
    deleteIntegrationIsPending,
    testConnection,
    testConnectionIsPending,
  } = useIntegrationProvider({
    providerId,
    workspaceId,
    grantType: provider.grant_type,
  })
  const { metadata, integration_status: integrationStatus } = provider

  // Check if actually connected based on backend status
  const isConnected = integrationStatus === "connected"
  const isConfigured = integrationStatus === "configured"

  const handleConfigSuccess = useCallback(() => {
    // Show connect prompt if configured but not connected
    if (integrationStatus === "configured") {
      setShowConnectPrompt(true)
    }
    // Stay on configuration tab after saving
  }, [integrationStatus])

  const handleOAuthConnect = useCallback(async () => {
    await connectProvider(providerId)
  }, [connectProvider, providerId])

  const handleDisconnect = useCallback(async () => {
    await disconnectProvider(providerId)
  }, [disconnectProvider, providerId])

  const handleDeleteIntegration = useCallback(async () => {
    await deleteIntegration(providerId)
    // Redirect to integrations list if it's a custom provider (since it will be deleted)
    if (isCustom) {
      router.push(`/workspaces/${workspaceId}/integrations`)
    }
  }, [deleteIntegration, providerId, isCustom, router, workspaceId])

  const handleTestConnection = useCallback(async () => {
    await testConnection(providerId)
  }, [testConnection, providerId])

  const handleRefreshCredentials = useCallback(async () => {
    if (isAuthCodeGrant) {
      await handleOAuthConnect()
    } else {
      await handleTestConnection()
    }
  }, [handleOAuthConnect, handleTestConnection, isAuthCodeGrant])

  const connectOrRefreshIsPending = isAuthCodeGrant
    ? connectProviderIsPending
    : testConnectionIsPending

  const isEnabled = Boolean(metadata.enabled)
  // Show delete button for custom providers even if not configured, since deletion removes the provider definition
  const showDeleteButton = isConnected || isConfigured || isCustom
  const isDeleteDisabled = !isEnabled || deleteIntegrationIsPending

  const DeleteIntegrationButton = ({
    compact = false,
  }: {
    compact?: boolean
  }) => {
    if (!showDeleteButton) {
      return null
    }

    const variant = compact ? "ghost" : "outline"
    const size = compact ? "sm" : "default"
    const iconClass = compact ? "h-3 w-3" : "h-4 w-4"
    const iconMargin = compact ? "mr-1" : "mr-2"
    const buttonClassName = compact
      ? "h-[22px] px-2 py-0 text-xs font-medium text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
      : "gap-2 border-destructive/40 text-destructive hover:bg-destructive/10"

    return (
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button
            variant={variant}
            size={size}
            className={cn(buttonClassName)}
            disabled={isDeleteDisabled}
          >
            {deleteIntegrationIsPending ? (
              <Loader2 className={cn(iconMargin, iconClass, "animate-spin")} />
            ) : (
              <Trash className={cn(iconMargin, iconClass)} />
            )}
            Delete connection
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete integration</AlertDialogTitle>
            <AlertDialogDescription>
              {isCustom ? (
                <>
                  Deleting the connection for {metadata.name} will remove all
                  stored credentials, configuration, and the custom provider
                  definition. You will need to recreate the custom provider to
                  use it again. Continue?
                </>
              ) : (
                <>
                  Deleting the connection for {metadata.name} removes all stored
                  credentials and configuration. You will need to reconfigure
                  this integration to connect again. Continue?
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleDeleteIntegration}
            >
              Delete connection
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    )
  }

  return (
    <div className="container mx-auto max-w-4xl p-6 mb-20 mt-12">
      <Link
        href={`/workspaces/${workspaceId}/integrations`}
        className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-4"
      >
        <ChevronLeft className="mr-1 size-4" />
        Back to integrations
      </Link>
      {/* Header */}
      <div className="mb-8 flex items-start justify-between">
        <div className="flex items-start gap-4">
          <ProviderIcon providerId={metadata.id} className="size-12" />
          <div>
            <h1 className="text-3xl font-bold">{metadata.name}</h1>
            <p className="mt-1 text-muted-foreground">
              {metadata.description ||
                `Connect ${metadata.name} to enhance your workflows`}
            </p>
            <div className="mt-2 flex gap-2">
              <Badge variant="secondary" className="whitespace-nowrap">
                {isAuthCodeGrant ? (
                  <>
                    <User className="mr-1 size-3" />
                    Authorization code
                  </>
                ) : (
                  <>
                    <Key className="mr-1 size-3" />
                    Client credentials
                  </>
                )}
              </Badge>
              <div className="flex items-center gap-2">
                <Badge
                  variant="outline"
                  className={cn(statusStyles[integrationStatus].style)}
                >
                  {statusStyles[integrationStatus].label}
                </Badge>
                {isConnected ? (
                  <>
                    {isAuthCodeGrant && (
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-[22px] px-2 py-0 text-xs font-medium text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                            disabled={!isEnabled || disconnectProviderIsPending}
                          >
                            {disconnectProviderIsPending ? (
                              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                            ) : (
                              <Unplug className="mr-1 h-3 w-3" />
                            )}
                            Disconnect
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>
                              Disconnect integration
                            </AlertDialogTitle>
                            <AlertDialogDescription>
                              Are you sure you want to disconnect from{" "}
                              {metadata.name}? This will remove your
                              authentication and you'll need to reconnect to use
                              this integration.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              variant="destructive"
                              onClick={handleDisconnect}
                            >
                              Disconnect
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-[22px] px-2 py-0 text-xs font-medium"
                      onClick={handleRefreshCredentials}
                      disabled={!isEnabled || connectOrRefreshIsPending}
                    >
                      {connectOrRefreshIsPending ? (
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      ) : isAuthCodeGrant ? (
                        <ExternalLink className="mr-1 h-3 w-3" />
                      ) : (
                        <Zap className="mr-1 h-3 w-3" />
                      )}
                      {isAuthCodeGrant
                        ? "Reconnect with OAuth"
                        : "Refresh token"}
                    </Button>
                    <DeleteIntegrationButton compact />
                  </>
                ) : isConfigured ? (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-[22px] px-2 py-0 text-xs font-medium"
                      onClick={
                        isAuthCodeGrant
                          ? handleOAuthConnect
                          : handleTestConnection
                      }
                      disabled={!isEnabled || connectOrRefreshIsPending}
                    >
                      {connectOrRefreshIsPending ? (
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      ) : isAuthCodeGrant ? (
                        <ExternalLink className="mr-1 h-3 w-3" />
                      ) : (
                        <Zap className="mr-1 h-3 w-3" />
                      )}
                      {isAuthCodeGrant ? "Connect with OAuth" : "Fetch token"}
                    </Button>
                    <DeleteIntegrationButton compact />
                  </>
                ) : (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-[22px] px-2 py-0 text-xs font-medium"
                      onClick={
                        isSelfConfiguringMCP
                          ? handleOAuthConnect
                          : () => handleTabChange("configuration")
                      }
                      disabled={
                        !isEnabled ||
                        (isSelfConfiguringMCP && connectProviderIsPending)
                      }
                    >
                      {isSelfConfiguringMCP && connectProviderIsPending ? (
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      ) : isSelfConfiguringMCP ? (
                        <ExternalLink className="mr-1 h-3 w-3" />
                      ) : (
                        <Settings className="mr-1 h-3 w-3" />
                      )}
                      {isSelfConfiguringMCP ? "Connect" : "Configure"}
                    </Button>
                    {isCustom && <DeleteIntegrationButton compact />}
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onValueChange={handleTabChange}
        className="space-y-6"
      >
        <TabsList className="h-8 justify-start rounded-none bg-transparent p-0 border-b border-border w-full">
          {!isCustom && (
            <TabsTrigger
              className="flex h-full min-w-24 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              value="overview"
            >
              <LayoutListIcon className="mr-2 size-4" />
              <span>Overview</span>
            </TabsTrigger>
          )}
          {!isSelfConfiguringMCP && (
            <TabsTrigger
              className="flex h-full min-w-24 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              value="configuration"
            >
              <Settings className="mr-2 size-4" />
              <span>Configuration</span>
            </TabsTrigger>
          )}
        </TabsList>

        {!isCustom && (
          <TabsContent value="overview" className="space-y-6">
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              {/* Main Content */}
              <div className="space-y-6 lg:col-span-2">
                {/* Connection Status */}
                {isConnected ? (
                  <Card>
                    <CardContent className="pt-6 space-y-4">
                      {integration && (
                        <div className="flex flex-col gap-3 text-sm">
                          <div>
                            <span className="font-medium">Token type:</span>{" "}
                            <span className="text-muted-foreground">
                              {integration.token_type}
                            </span>
                          </div>
                          {integration.expires_at && (
                            <div>
                              <span className="font-medium">Expires:</span>{" "}
                              <span className="text-muted-foreground">
                                {new Date(
                                  integration.expires_at
                                ).toLocaleString()}
                              </span>
                              <span className="ml-2 text-xs text-muted-foreground">
                                (auto-refreshed)
                              </span>
                            </div>
                          )}

                          {/* Scopes Section */}
                          {integration.granted_scopes &&
                            integration.granted_scopes.length > 0 && (
                              <div>
                                <div className="font-medium mb-2">
                                  Granted scopes:
                                </div>
                                <div className="flex flex-wrap gap-1">
                                  {integration.granted_scopes.map((scope) => (
                                    <Badge
                                      key={scope}
                                      variant="outline"
                                      className="text-xs bg-green-50 text-green-700 border-green-200"
                                    >
                                      {scope}
                                    </Badge>
                                  ))}
                                </div>
                              </div>
                            )}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ) : null}

                {/* OAuth Redirect URI */}
                {provider.grant_type === "authorization_code" &&
                  provider.redirect_uri && (
                    <div className="space-y-3">
                      <h3 className="text-lg font-semibold">
                        OAuth redirect URI
                      </h3>
                      <RedirectUriDisplay redirectUri={provider.redirect_uri} />
                    </div>
                  )}
              </div>

              {/* Sidebar */}
              {!isCustom && (
                <div className="space-y-6">
                  {/* Documentation */}
                  <Card>
                    <CardHeader>
                      <CardTitle>Documentation</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {metadata.api_docs_url && (
                        <Button
                          variant="outline"
                          className="w-full justify-start"
                          asChild
                        >
                          <a
                            href={metadata.api_docs_url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <ExternalLink className="mr-2 size-4" />
                            API docs
                          </a>
                        </Button>
                      )}
                      {metadata.setup_guide_url && (
                        <Button
                          variant="outline"
                          className="w-full justify-start"
                          asChild
                        >
                          <a
                            href={metadata.setup_guide_url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <ExternalLink className="mr-2 size-4" />
                            Setup guide
                          </a>
                        </Button>
                      )}
                      {metadata.troubleshooting_url && (
                        <Button
                          variant="outline"
                          className="w-full justify-start"
                          asChild
                        >
                          <a
                            href={metadata.troubleshooting_url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <ExternalLink className="mr-2 size-4" />
                            Troubleshooting
                          </a>
                        </Button>
                      )}
                      {!metadata.api_docs_url &&
                        !metadata.setup_guide_url &&
                        !metadata.troubleshooting_url && (
                          <p className="text-sm text-muted-foreground">
                            No documentation links available for this provider.
                          </p>
                        )}
                    </CardContent>
                  </Card>
                </div>
              )}
            </div>
          </TabsContent>
        )}

        {!isSelfConfiguringMCP && (
          <TabsContent value="configuration" className="space-y-6">
            {/* Configuration Form */}
            <div className="space-y-4">
              <ProviderConfigForm
                provider={provider}
                onSuccess={handleConfigSuccess}
                additionalButtons={
                  isConfigured ? (
                    <div className="flex flex-wrap items-center gap-2">
                      {provider.grant_type === "client_credentials" ? (
                        <Button
                          onClick={handleTestConnection}
                          disabled={!isEnabled || testConnectionIsPending}
                        >
                          {testConnectionIsPending ? (
                            <>
                              <Loader2 className="mr-2 size-4 animate-spin" />
                              Testing...
                            </>
                          ) : (
                            <>
                              <Zap className="mr-2 size-4" />
                              Test connection
                            </>
                          )}
                        </Button>
                      ) : (
                        <Button
                          onClick={handleOAuthConnect}
                          disabled={!isEnabled || connectProviderIsPending}
                        >
                          {connectProviderIsPending ? (
                            <>
                              <Loader2 className="mr-2 size-4 animate-spin" />
                              Connecting...
                            </>
                          ) : (
                            <>
                              <ExternalLink className="mr-2 size-4" />
                              Connect with OAuth
                            </>
                          )}
                        </Button>
                      )}
                    </div>
                  ) : null
                }
              />
            </div>
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
