"use client"

import { useState, useMemo } from "react"
import { useParams } from "next/navigation"
import Link from "next/link"
import { ProviderMetadata } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import {
  ChevronLeft,
  CheckCircle,
  AlertCircle,
  ExternalLink,
  Settings,
  Zap,
  Shield,
  Database,
  Loader2,
} from "lucide-react"

import { useIntegrations } from "@/lib/hooks"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { ProviderConfigForm } from "@/components/provider-config-form"

// Mock data - same as in the main page
const providerMetadata: Record<string, { icon: string; category: string; features: string[]; setupSteps: string[] }> = {
  microsoft: {
    icon: "🔷",
    category: "auth",
    features: ["OAuth 2.0", "Azure AD Integration", "Microsoft Graph API", "Single Sign-On"],
    setupSteps: [
      "Register your application in Azure Portal",
      "Configure OAuth redirect URIs",
      "Add client ID and secret",
      "Test the connection",
    ],
  },
  google: {
    icon: "🔵",
    category: "auth",
    features: ["OAuth 2.0", "Google Workspace", "Gmail API", "Drive Integration"],
    setupSteps: [
      "Create a project in Google Cloud Console",
      "Enable required APIs",
      "Configure OAuth consent screen",
      "Test the integration",
    ],
  },
  github: {
    icon: "🐙",
    category: "auth",
    features: ["Repository Access", "Automated Deployments", "Issue Tracking", "Pull Requests"],
    setupSteps: [
      "Create a GitHub OAuth App",
      "Configure callback URL",
      "Add client credentials",
      "Authorize repository access",
    ],
  },
  slack: {
    icon: "💬",
    category: "communication",
    features: ["Channel Notifications", "Direct Messages", "Custom Webhooks", "Bot Integration"],
    setupSteps: [
      "Create a Slack App",
      "Configure OAuth scopes",
      "Install app to workspace",
      "Test notifications",
    ],
  },
  aws: {
    icon: "☁️",
    category: "cloud",
    features: ["S3 Storage", "Lambda Functions", "CloudWatch Logs", "IAM Management"],
    setupSteps: [
      "Create IAM user with programmatic access",
      "Attach required policies",
      "Generate access keys",
      "Configure AWS region",
    ],
  },
  datadog: {
    icon: "📊",
    category: "monitoring",
    features: ["Metrics Collection", "Log Aggregation", "APM Tracing", "Alerting"],
    setupSteps: [
      "Get your Datadog API key",
      "Configure application key",
      "Select monitoring regions",
      "Verify data collection",
    ],
  },
  pagerduty: {
    icon: "🚨",
    category: "alerting",
    features: ["Incident Management", "On-Call Scheduling", "Alert Routing", "Escalation Policies"],
    setupSteps: [
      "Create PagerDuty integration key",
      "Configure service routing",
      "Set up escalation policies",
      "Test alert delivery",
    ],
  },
  default: {
    icon: "🔌",
    category: "other",
    features: ["API Integration", "Webhook Support", "Custom Configuration"],
    setupSteps: [
      "Configure API credentials",
      "Set up webhook endpoints",
      "Test the connection",
      "Verify integration",
    ],
  },
}

const categoryColors: Record<string, string> = {
  auth: "bg-green-100 text-green-800",
  communication: "bg-pink-100 text-pink-800",
  cloud: "bg-blue-100 text-blue-800",
  monitoring: "bg-orange-100 text-orange-800",
  alerting: "bg-red-100 text-red-800",
  other: "bg-gray-100 text-gray-800",
}

function getProviderInfo(providerId: string) {
  return providerMetadata[providerId.toLowerCase()] || providerMetadata.default
}

export default function IntegrationDetailPage() {
  const params = useParams()
  const { workspaceId } = useWorkspace()
  const integrationId = params.id as string

  const [isConfigDialogOpen, setIsConfigDialogOpen] = useState(false)
  const [connectionStatus, setConnectionStatus] = useState<"idle" | "connecting" | "success" | "error">("idle")
  const [errorMessage, setErrorMessage] = useState("")

  const {
    integrations,
    integrationsIsLoading,
    providers,
    providersIsLoading,
    connectProvider,
    connectProviderIsPending,
    disconnectProvider,
    disconnectProviderIsPending,
    configureProviderIsPending,
  } = useIntegrations(workspaceId)

  // Find the provider and integration
  const provider = providers?.find(p => p.id === integrationId)
  const integration = integrations?.find(i => i.provider_id === integrationId)
  const providerInfo = getProviderInfo(integrationId)

  // Enhanced provider with metadata
  const enhancedProvider = useMemo(() => {
    if (!provider) return null
    return {
      ...provider,
      ...providerInfo,
      status: integration ? "connected" : "available",
    }
  }, [provider, providerInfo, integration])

  const handleOAuthConnect = async () => {
    try {
      setConnectionStatus("connecting")
      await connectProvider(integrationId)
      setConnectionStatus("success")
    } catch (error) {
      setConnectionStatus("error")
      setErrorMessage("Failed to connect. Please try again.")
    }
  }

  const handleDisconnect = async () => {
    try {
      await disconnectProvider(integrationId)
      setConnectionStatus("idle")
    } catch (error) {
      setErrorMessage("Failed to disconnect. Please try again.")
    }
  }

  const openConfigDialog = () => {
    setIsConfigDialogOpen(true)
  }

  if (providersIsLoading || integrationsIsLoading) {
    return (
      <div className="container mx-auto max-w-4xl p-6">
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-8 animate-spin text-muted-foreground" />
        </div>
      </div>
    )
  }

  if (!enhancedProvider) {
    return (
      <div className="container mx-auto max-w-4xl p-6">
        <div className="py-12 text-center">
          <div className="text-muted-foreground">Integration not found.</div>
          <Link href={`/workspaces/${workspaceId}/integrations`}>
            <Button variant="outline" className="mt-4">
              <ChevronLeft className="mr-2 size-4" />
              Back to Integrations
            </Button>
          </Link>
        </div>
      </div>
    )
  }

  const isConnected = integration !== undefined
  const requiresOAuth = ["microsoft", "google", "github", "slack"].includes(integrationId.toLowerCase())

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
            <BreadcrumbPage>{enhancedProvider.name}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      {/* Header */}
      <div className="mb-8 flex items-start justify-between">
        <div className="flex items-center gap-4">
          <div className="text-4xl">{enhancedProvider.icon}</div>
          <div>
            <h1 className="text-3xl font-bold">{enhancedProvider.name}</h1>
            <p className="mt-1 text-muted-foreground">
              {enhancedProvider.description || `Connect ${enhancedProvider.name} to enhance your workflows`}
            </p>
            <div className="mt-2 flex gap-2">
              <Badge className={categoryColors[enhancedProvider.category]}>
                {enhancedProvider.category}
              </Badge>
              <Badge className={isConnected ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-800"}>
                {isConnected ? "Connected" : "Not Connected"}
              </Badge>
            </div>
          </div>
        </div>
        <Link href={`/workspaces/${workspaceId}/integrations`}>
          <Button variant="outline">
            <ChevronLeft className="mr-2 size-4" />
            Back
          </Button>
        </Link>
      </div>

      {/* Status Alert */}
      {connectionStatus === "success" && (
        <Alert className="mb-6 border-green-200 bg-green-50">
          <CheckCircle className="size-4 text-green-600" />
          <AlertDescription className="text-green-800">
            Successfully connected to {enhancedProvider.name}!
          </AlertDescription>
        </Alert>
      )}

      {connectionStatus === "error" && (
        <Alert className="mb-6 border-red-200 bg-red-50">
          <AlertCircle className="size-4 text-red-600" />
          <AlertDescription className="text-red-800">{errorMessage}</AlertDescription>
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
                  <div className="space-y-1 text-sm text-muted-foreground">
                    <div>Token Type: {integration.token_type}</div>
                    {integration.expires_at && (
                      <div>Expires: {new Date(integration.expires_at).toLocaleDateString()}</div>
                    )}
                  </div>
                  <Button
                    variant="destructive"
                    onClick={handleDisconnect}
                    disabled={disconnectProviderIsPending}
                  >
                    {disconnectProviderIsPending ? "Disconnecting..." : "Disconnect"}
                  </Button>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="text-muted-foreground">
                    This integration is not connected to your workspace.
                  </div>

                  {requiresOAuth ? (
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 rounded-lg bg-blue-50 p-3">
                        <Shield className="size-4 text-blue-600" />
                        <span className="text-sm text-blue-800">
                          This integration uses OAuth for secure authentication
                        </span>
                      </div>
                      <Button
                        onClick={handleOAuthConnect}
                        disabled={connectProviderIsPending}
                        className="w-full sm:w-auto"
                      >
                        {connectProviderIsPending ? (
                          <>
                            <Loader2 className="mr-2 size-4 animate-spin" />
                            Connecting...
                          </>
                        ) : (
                          <>
                            <ExternalLink className="mr-2 size-4" />
                            Connect with {enhancedProvider.name}
                          </>
                        )}
                      </Button>
                    </div>
                  ) : (
                    <Button
                      onClick={openConfigDialog}
                      className="w-full sm:w-auto"
                    >
                      <Settings className="mr-2 size-4" />
                      Configure Integration
                    </Button>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Configuration */}
          {!requiresOAuth && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Settings className="size-5" />
                  Configuration
                </CardTitle>
                <CardDescription>
                  Configure your {enhancedProvider.name} integration settings
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Button
                  onClick={openConfigDialog}
                  variant="outline"
                  disabled={isConnected}
                >
                  {isConnected ? "Configuration Locked (Disconnect First)" : "Open Configuration"}
                </Button>
              </CardContent>
            </Card>
          )}

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
                {enhancedProvider.setupSteps.map((step, index) => (
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
                {enhancedProvider.features.map((feature, index) => (
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
      {enhancedProvider && (
        <ProviderConfigForm
          provider={enhancedProvider as ProviderMetadata}
          isOpen={isConfigDialogOpen}
          onClose={() => setIsConfigDialogOpen(false)}
          isLoading={configureProviderIsPending}
        />
      )}
    </div>
  )
}
