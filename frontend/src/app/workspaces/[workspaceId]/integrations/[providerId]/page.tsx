"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { useWorkspace } from "@/providers/workspace"
import {
  AlertCircle,
  CheckCircle,
  ChevronLeft,
  Database,
  ExternalLink,
  Loader2,
  Settings,
  Shield,
  Zap,
} from "lucide-react"

import { useIntegrationProvider, useIntegrations } from "@/lib/hooks"
import { categoryColors } from "@/lib/provider-styles"
import { cn } from "@/lib/utils"
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
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ProviderConfigForm } from "@/components/provider-config-form"
import { RedirectUriDisplay } from "@/components/redirect-uri-display"


export default function ProviderDetailPage() {
  const params = useParams()
  const { workspaceId } = useWorkspace()
  const providerId = params.providerId as string

  const [isConfigDialogOpen, setIsConfigDialogOpen] = useState(false)
  const [isConnecting, setIsConnecting] = useState(false)
  const [errorMessage, setErrorMessage] = useState("")
  const [showConnectPrompt, setShowConnectPrompt] = useState(false)
  const [showSuccessMessage, setShowSuccessMessage] = useState(false)

  const {
    integration,
    integrationIsLoading,
    connectProvider,
    connectProviderIsPending,
    disconnectProvider,
    disconnectProviderIsPending,
  } = useIntegrationProvider({ providerId, workspaceId })

  const { providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)

  // Find the provider that matches the current providerId from the URL
  const provider = useMemo(
    () => providers?.find((p) => p.metadata.id === providerId),
    [providers, providerId]
  )

  const handleOAuthConnect = async () => {
    try {
      setIsConnecting(true)
      setErrorMessage("")
      await connectProvider(providerId)
      setShowSuccessMessage(true)
      // Hide success message after 5 seconds
      setTimeout(() => setShowSuccessMessage(false), 5000)
    } catch (error) {
      setErrorMessage("Failed to connect. Please try again.")
    } finally {
      setIsConnecting(false)
    }
  }

  const handleDisconnect = async () => {
    try {
      await disconnectProvider(providerId)
      setShowSuccessMessage(false)
      setErrorMessage("")
    } catch (error) {
      setErrorMessage("Failed to disconnect. Please try again.")
    }
  }

  const openConfigDialog = () => {
    setIsConfigDialogOpen(true)
  }

  const handleConfigSuccess = () => {
    setIsConfigDialogOpen(false)
    // Show connect prompt if configured but not connected
    if (integration_status === "configured") {
      setShowConnectPrompt(true)
    }
  }

  if (providersIsLoading) {
    return <CenteredSpinner />
  }

  if (providersError) {
    return <div>Error: {providersError.message}</div>
  }
  if (!provider) {
    return (
      <div className="container mx-auto max-w-4xl p-6">
        <div className="flex flex-col items-center justify-center space-y-4 py-12">
          <AlertCircle className="size-12 text-muted-foreground" />
          <h2 className="text-xl font-semibold">Provider not found</h2>
          <div className="text-muted-foreground">
            The requested integration provider could not be found.
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
  const { metadata, integration_status } = provider ?? {}
  const isEnabled = metadata?.enabled !== false

  // Check if actually connected based on backend status
  const isConnected = integration_status === "connected"
  const isConfigured =
    integration_status === "configured" || integration_status === "connected"

  return (
    <div className="container mx-auto max-w-4xl p-6">
      {/* Breadcrumb */}
      <Breadcrumb className="mb-6">
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href={`/workspaces/${workspaceId}/integrations`}>
              Integrations
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
            <Badge
              className={cn(
                integration_status === "connected" &&
                  "bg-green-100 text-green-800 hover:bg-green-200",
                integration_status === "configured" &&
                  "bg-yellow-100 text-yellow-800 hover:bg-yellow-200",
                (!integration_status ||
                  (integration_status !== "connected" &&
                    integration_status !== "configured")) &&
                  "bg-gray-100 text-gray-800 hover:bg-gray-200"
              )}
            >
              {integration_status === "connected"
                ? "Connected"
                : integration_status === "configured"
                  ? "Configured"
                  : "Not Configured"}
            </Badge>
          </div>
        </div>
        <Link href={`/workspaces/${workspaceId}/integrations`}>
          <Button variant="outline">
            <ChevronLeft className="mr-2 size-4" />
            Back
          </Button>
        </Link>
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

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Main Content */}
        <div className="space-y-6 lg:col-span-2">
          {/* Connection Status */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Zap className="size-5" />
                Connection Status
              </CardTitle>
            </CardHeader>
            <CardContent>
              {isConnected ? (
                <div className="space-y-4">
                  <div className="flex items-center gap-2 text-green-600">
                    <CheckCircle className="size-5" />
                    <span className="font-medium">Connected</span>
                  </div>
                  {integration && (
                    <div className="space-y-1 text-sm text-muted-foreground">
                      <div>Token Type: {integration.token_type}</div>
                      {integration.expires_at && (
                        <div>
                          Expires:{" "}
                          {new Date(
                            integration.expires_at
                          ).toLocaleDateString()}
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
                  <div className="text-muted-foreground">
                    {integration_status === "configured"
                      ? "This integration is configured but not connected. Complete the OAuth flow to start using it."
                      : "This integration is not connected to your workspace. Configure it with your client credentials or use OAuth for quick setup."}
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Button
                      onClick={openConfigDialog}
                      className="sm:w-auto"
                      disabled={!isEnabled}
                    >
                      <Settings className="mr-2 size-4" />
                      {integration_status === "configured"
                        ? "Update Configuration"
                        : "Configure Integration"}
                    </Button>

                    {isConfigured && (
                      <Button
                        onClick={handleOAuthConnect}
                        disabled={!isEnabled || connectProviderIsPending || isConnecting}
                        variant="outline"
                        className="sm:w-auto"
                      >
                        {connectProviderIsPending || isConnecting ? (
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

          {/* Configuration */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Settings className="size-5" />
                Configuration Details
              </CardTitle>
              <CardDescription>
                Manage your {metadata.name} integration settings and credentials
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <h4 className="mb-2 text-sm font-medium">OAuth Redirect URI</h4>
                <RedirectUriDisplay redirectUri={provider.redirect_uri} />
              </div>
              <Button
                onClick={openConfigDialog}
                variant="outline"
                disabled={!isEnabled}
              >
                {isConnected ? "Update Configuration" : "Open Configuration"}
              </Button>
            </CardContent>
          </Card>

          {/* Setup Steps */}
          <Card>
            <CardHeader>
              <CardTitle>Setup Guide</CardTitle>
              <CardDescription>
                Follow these steps to complete the integration
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {metadata.setup_steps?.map((step, index) => (
                  <div key={index} className="flex items-start gap-3">
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
            </CardContent>
          </Card>
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* Features */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database className="size-5" />
                Features
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {metadata.features?.map((feature, index) => (
                  <div key={index} className="flex items-center gap-2 text-sm">
                    <CheckCircle className="size-4 shrink-0 text-green-500" />
                    <span>{feature}</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Documentation */}
          <Card>
            <CardHeader>
              <CardTitle>Documentation</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Button variant="outline" className="w-full justify-start">
                <ExternalLink className="mr-2 size-4" />
                API Documentation
              </Button>
              <Button variant="outline" className="w-full justify-start">
                <ExternalLink className="mr-2 size-4" />
                Setup Guide
              </Button>
              <Button variant="outline" className="w-full justify-start">
                <ExternalLink className="mr-2 size-4" />
                Troubleshooting
              </Button>
            </CardContent>
          </Card>

          {/* Support */}
          <Card>
            <CardHeader>
              <CardTitle>Need Help?</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="mb-3 text-sm text-muted-foreground">
                Having trouble with this integration? We&apos;re here to help.
              </p>
              <Button variant="outline" className="w-full">
                Contact Support
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Provider Configuration Form */}
      {metadata && (
        <ProviderConfigForm
          provider={metadata}
          isOpen={isConfigDialogOpen}
          onClose={() => setIsConfigDialogOpen(false)}
          onSuccess={handleConfigSuccess}
        />
      )}
    </div>
  )
}
