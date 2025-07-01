"use client"

import {
  AlertCircle,
  CheckCircle,
  ChevronLeft,
  ExternalLink,
  LayoutListIcon,
  Loader2,
  Settings,
  Shield,
  Zap,
} from "lucide-react"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useCallback, useState } from "react"
import type { IntegrationStatus, ProviderRead } from "@/client"
import { ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ProviderConfigForm } from "@/components/provider-config-form"
import { RedirectUriDisplay } from "@/components/redirect-uri-display"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { CollapsibleCard } from "@/components/ui/collapsible-card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useIntegrationProvider } from "@/lib/hooks"
import { categoryColors } from "@/lib/provider-styles"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

export default function ProviderDetailPage() {
  const params = useParams()
  const { workspaceId } = useWorkspace()
  const providerId = params.providerId as string

  const { provider, providerIsLoading, providerError } = useIntegrationProvider(
    {
      providerId,
      workspaceId,
    }
  )

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

const statusStyles: Record<
  IntegrationStatus,
  {
    label: string
    style: string
  }
> = {
  connected: {
    label: "Connected",
    style: "bg-green-100 text-green-800 hover:bg-green-200",
  },
  configured: {
    label: "Configured",
    style: "bg-yellow-100 text-yellow-800 hover:bg-yellow-200",
  },
  not_configured: {
    label: "Not Configured",
    style: "bg-gray-100 text-gray-800 hover:bg-gray-200",
  },
}

type ProviderDetailTab = "overview" | "configuration"

function ProviderDetailContent({ provider }: { provider: ProviderRead }) {
  const { workspaceId } = useWorkspace()
  const router = useRouter()
  const searchParams = useSearchParams()
  const [errorMessage, setErrorMessage] = useState("")
  const [showConnectPrompt, setShowConnectPrompt] = useState(false)
  const [showSuccessMessage, setShowSuccessMessage] = useState(false)
  const providerId = provider.metadata.id

  // Get active tab from URL query params, default to "overview"
  const activeTab = (
    ["overview", "configuration"].includes(searchParams.get("tab") || "")
      ? searchParams.get("tab")
      : "overview"
  ) as ProviderDetailTab

  // Function to handle tab changes and update URL
  const handleTabChange = useCallback(
    (tab: string) => {
      router.push(
        `/workspaces/${workspaceId}/integrations/${providerId}?tab=${tab}`
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
  } = useIntegrationProvider({ providerId, workspaceId })
  const { metadata, integration_status: integrationStatus } = provider

  // Check if actually connected based on backend status
  const isConnected = integrationStatus === "connected"
  const isConfigured = integrationStatus === "configured"

  const handleConfigSuccess = useCallback(() => {
    // Show connect prompt if configured but not connected
    if (integrationStatus === "configured") {
      setShowConnectPrompt(true)
    }
    // Switch back to overview tab after successful configuration
    handleTabChange("overview")
  }, [integrationStatus, handleTabChange])

  const handleOAuthConnect = useCallback(async () => {
    try {
      setErrorMessage("")
      await connectProvider(providerId)
      setShowSuccessMessage(true)
      // Hide success message after 5 seconds
      setTimeout(() => setShowSuccessMessage(false), 5000)
    } catch (_error) {
      setErrorMessage("Failed to connect. Please try again.")
    }
  }, [connectProvider, providerId])

  const handleDisconnect = useCallback(async () => {
    try {
      await disconnectProvider(providerId)
      setShowSuccessMessage(false)
      setErrorMessage("")
    } catch (_error) {
      setErrorMessage("Failed to disconnect. Please try again.")
    }
  }, [disconnectProvider, providerId])

  const isEnabled = Boolean(metadata.enabled)

  return (
    <div className="container mx-auto max-w-4xl p-6 min-h-screen">
      {/* Breadcrumb */}
      <Breadcrumb className="mb-6">
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink asChild>
              <Link href={`/workspaces/${workspaceId}/integrations`}>
                Integrations
              </Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{metadata.name}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      {/* Header */}
      <div className="mb-8 flex items-start justify-between">
        <div className="flex items-start gap-4">
          <ProviderIcon providerId={metadata.id} className="size-10 p-2" />
          <div>
            <h1 className="text-3xl font-bold">{metadata.name}</h1>
            <p className="mt-1 text-muted-foreground">
              {metadata.description ||
                `Connect ${metadata.name} to enhance your workflows`}
            </p>
            <div className="mt-2 flex gap-2">
              {metadata.categories?.map((category) => (
                <Badge
                  key={category}
                  className={categoryColors[category || "other"]}
                >
                  {category}
                </Badge>
              ))}
              <Badge className={cn(statusStyles[integrationStatus].style)}>
                {statusStyles[integrationStatus].label}
              </Badge>
            </div>
          </div>
        </div>
      </div>

      {/* Disabled Provider Alert */}
      {!isEnabled && (
        <Alert className="mb-6 border-orange-200 bg-orange-50">
          <AlertCircle className="size-4 text-orange-600" />
          <AlertDescription className="text-orange-800">
            This integration is coming soon and is not yet available for use.
          </AlertDescription>
        </Alert>
      )}

      {/* Status Alert */}
      {showSuccessMessage && isConnected && (
        <Alert className="mb-6 border-green-200 bg-green-50">
          <CheckCircle className="size-4 text-green-600" />
          <AlertDescription className="text-green-800">
            Successfully connected to {metadata.name}!
          </AlertDescription>
        </Alert>
      )}

      {errorMessage && (
        <Alert className="mb-6 border-red-200 bg-red-50">
          <AlertCircle className="size-4 text-red-600" />
          <AlertDescription className="text-red-800">
            {errorMessage}
          </AlertDescription>
        </Alert>
      )}

      {showConnectPrompt && !isConnected && (
        <Alert className="mb-6 border-blue-200 bg-blue-50">
          <Shield className="size-4 text-blue-600" />
          <AlertDescription className="flex items-center justify-between">
            <span className="text-blue-800">
              Configuration saved! Ready to connect with OAuth?
            </span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowConnectPrompt(false)}
              >
                Later
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  setShowConnectPrompt(false)
                  handleOAuthConnect()
                }}
              >
                Connect Now
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onValueChange={handleTabChange}
        className="space-y-6"
      >
        <TabsList className="h-8 justify-start rounded-none bg-transparent p-0 border-b border-border w-full">
          <TabsTrigger
            className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            value="overview"
          >
            <LayoutListIcon className="mr-2 size-4" />
            <span>Overview</span>
          </TabsTrigger>
          <TabsTrigger
            className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            value="configuration"
          >
            <Settings className="mr-2 size-4" />
            <span>Configuration</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-6">
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            {/* Main Content */}
            <div className="space-y-6 lg:col-span-2">
              {/* Connection Status */}
              <Card className="border-border">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Zap className="size-5" />
                    Connection Status
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {isConnected ? (
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 text-green-600">
                        <CheckCircle className="size-5" />
                        <span className="font-medium">Connected</span>
                      </div>
                      {integration && (
                        <div className="space-y-3 text-sm text-muted-foreground">
                          <div>Token Type: {integration.token_type}</div>
                          {integration.expires_at && (
                            <div>
                              Expires:{" "}
                              {new Date(
                                integration.expires_at
                              ).toLocaleDateString()}
                            </div>
                          )}

                          {/* Scopes Section */}
                          {integration.granted_scopes && (
                            <div className="space-y-2">
                              {integration.granted_scopes &&
                                integration.granted_scopes.length > 0 && (
                                  <div>
                                    <div className="font-medium text-foreground mb-1">
                                      Granted Scopes:
                                    </div>
                                    <div className="flex flex-wrap gap-1">
                                      {integration.granted_scopes.map(
                                        (scope) => (
                                          <Badge
                                            key={scope}
                                            variant="outline"
                                            className="text-xs bg-green-50 text-green-700 border-green-200"
                                          >
                                            {scope}
                                          </Badge>
                                        )
                                      )}
                                    </div>
                                  </div>
                                )}
                            </div>
                          )}
                        </div>
                      )}
                      <Button
                        variant="destructive"
                        onClick={handleDisconnect}
                        disabled={!isEnabled || disconnectProviderIsPending}
                      >
                        {disconnectProviderIsPending
                          ? "Disconnecting..."
                          : "Disconnect"}
                      </Button>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      <div className="text-sm text-muted-foreground">
                        {isConfigured
                          ? "This integration is configured but not connected. Complete the OAuth flow to start using it."
                          : "This integration is not connected to your workspace. Configure it with your client credentials or use OAuth for quick setup."}
                      </div>

                      <div className="flex flex-wrap gap-2">
                        <Button
                          onClick={() => handleTabChange("configuration")}
                          className="sm:w-auto"
                          disabled={!isEnabled}
                        >
                          <Settings className="mr-2 size-4" />
                          {isConfigured
                            ? "Update Configuration"
                            : "Configure Integration"}
                        </Button>

                        {isConfigured && (
                          <Button
                            onClick={handleOAuthConnect}
                            disabled={!isEnabled || connectProviderIsPending}
                            variant="outline"
                            className="sm:w-auto"
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
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* OAuth Redirect URI */}
              <div className="space-y-3">
                <h3 className="text-lg font-semibold">OAuth Redirect URI</h3>
                <div className="text-sm text-muted-foreground">
                  Use this redirect URI when configuring your OAuth application
                  in the provider's developer console.
                </div>
                <RedirectUriDisplay redirectUri={provider.redirect_uri} />
              </div>

              {/* Setup Steps */}
              <CollapsibleCard
                title={
                  <div className="flex items-center gap-2">
                    Setup Guide
                    {isConnected && (
                      <>
                        <span className="text-sm font-normal text-muted-foreground">
                          (completed)
                        </span>
                        <CheckCircle className="size-4 text-green-600" />
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
                      <div
                        className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${
                          isConnected
                            ? "bg-green-100 text-green-800"
                            : "bg-gray-100 text-gray-600"
                        }`}
                      >
                        {isConnected ? (
                          <CheckCircle className="size-3" />
                        ) : (
                          index + 1
                        )}
                      </div>
                      <span
                        className={`text-sm ${
                          isConnected
                            ? "text-green-800 line-through"
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
                        API Documentation
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
                        Setup Guide
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

        <TabsContent value="configuration" className="space-y-6">
          {/* OAuth Redirect URI - Prominently displayed */}
          <div className="space-y-3">
            <h3 className="text-lg font-semibold">OAuth Redirect URI</h3>
            <div className="text-sm text-muted-foreground">
              Use this redirect URI when configuring your OAuth application in
              the provider's developer console.
            </div>
            <RedirectUriDisplay redirectUri={provider.redirect_uri} />
          </div>

          {/* Configuration Form */}
          <div className="space-y-4">
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <Settings className="size-5" />
              Configure {metadata.name}
            </h3>
            <p className="text-sm text-muted-foreground">
              Set up your {metadata.name} integration with OAuth credentials and
              custom settings
            </p>
            <ProviderConfigForm
              provider={provider}
              onSuccess={handleConfigSuccess}
            />
          </div>

          {/* Quick Actions */}
          <div className="flex flex-wrap gap-3">
            {isConfigured && (
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
        </TabsContent>
      </Tabs>
    </div>
  )
}
