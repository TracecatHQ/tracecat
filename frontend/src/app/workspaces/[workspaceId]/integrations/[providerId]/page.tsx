"use client"

import {
  AlertCircle,
  ChevronLeft,
  ExternalLink,
  Key,
  LayoutListIcon,
  Loader2,
  Settings,
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
import { SuccessIcon, statusStyles } from "@/components/integrations/icons"
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
import { CollapsibleCard } from "@/components/ui/collapsible-card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useIntegrationProvider } from "@/lib/hooks"
import { isMCPProvider } from "@/lib/providers"
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
  const requiresConfiguration = Boolean(provider.metadata.requires_config)
  const isSelfConfiguringMCP = isMCP && !requiresConfiguration

  // Get active tab from URL query params, default to "overview"
  // For MCP providers, always use "overview" since there's no configuration tab
  const activeTab = (
    searchParams &&
    ["overview", "configuration"].includes(searchParams.get("tab") || "") &&
    !isSelfConfiguringMCP // Don't allow configuration tab for self-configured MCP providers
      ? (searchParams.get("tab") ?? "overview")
      : "overview"
  ) as ProviderDetailTab

  // Function to handle tab changes and update URL
  const handleTabChange = useCallback(
    (tab: string) => {
      const params = new URLSearchParams(searchParams?.toString() || "")
      params.set("tab", tab)
      router.push(
        `/workspaces/${workspaceId}/integrations/${providerId}?${params.toString()}`
      )
    },
    [router, workspaceId, providerId, searchParams]
  )

  // Whether there's a connected integration
  const {
    integration,
    connectProvider,
    connectProviderIsPending,
    disconnectProvider,
    disconnectProviderIsPending,
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

  const handleTestConnection = useCallback(async () => {
    await testConnection(providerId)
  }, [testConnection, providerId])

  const isEnabled = Boolean(metadata.enabled)

  return (
    <div className="container mx-auto max-w-4xl p-6 mb-20 mt-12">
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
                {provider.grant_type === "client_credentials" ? (
                  <>
                    <Key className="mr-1 size-3" />
                    Client credentials
                  </>
                ) : (
                  <>
                    <User className="mr-1 size-3" />
                    Authorization code
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
                {isConnected && provider.grant_type === "authorization_code" ? (
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
                          {metadata.name}? This will remove your authentication
                          and you'll need to reconnect to use this integration.
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
                ) : isConfigured ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-[22px] px-2 py-0 text-xs font-medium"
                    onClick={
                      provider.grant_type === "authorization_code"
                        ? handleOAuthConnect
                        : handleTestConnection
                    }
                    disabled={
                      !isEnabled ||
                      connectProviderIsPending ||
                      testConnectionIsPending
                    }
                  >
                    {connectProviderIsPending || testConnectionIsPending ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : provider.grant_type === "authorization_code" ? (
                      <ExternalLink className="mr-1 h-3 w-3" />
                    ) : (
                      <Zap className="mr-1 h-3 w-3" />
                    )}
                    {provider.grant_type === "authorization_code"
                      ? "Connect with OAuth"
                      : "Fetch token"}
                  </Button>
                ) : (
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
          <TabsTrigger
            className="flex h-full min-w-24 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            value="overview"
          >
            <LayoutListIcon className="mr-2 size-4" />
            <span>Overview</span>
          </TabsTrigger>
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

              {/* Setup Steps */}
              <CollapsibleCard
                title={
                  <div className="flex items-center gap-2">
                    Setup guide
                    {isConnected && (
                      <>
                        <span className="text-sm font-normal text-muted-foreground">
                          (completed)
                        </span>
                        <SuccessIcon />
                      </>
                    )}
                  </div>
                }
                description="Follow these steps to complete the integration"
                defaultOpen={!isConnected}
              >
                <div className="space-y-3">
                  {metadata.setup_steps?.map((step, index) => (
                    <div key={step} className="flex items-start gap-3">
                      <div className="flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium bg-gray-100 text-gray-600">
                        {index + 1}
                      </div>
                      <span
                        className={`text-sm ${
                          isConnected
                            ? "text-gray-500 line-through"
                            : "text-gray-700"
                        }`}
                      >
                        {step}
                      </span>
                    </div>
                  ))}
                </div>
              </CollapsibleCard>
            </div>

            {/* Sidebar */}
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
          </div>
        </TabsContent>

        {!isSelfConfiguringMCP && (
          <TabsContent value="configuration" className="space-y-6">
            {/* Configuration Form */}
            <div className="space-y-4">
              <ProviderConfigForm
                provider={provider}
                onSuccess={handleConfigSuccess}
                additionalButtons={
                  isConfigured ? (
                    provider.grant_type === "client_credentials" ? (
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
                    )
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
