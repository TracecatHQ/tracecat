"use client"

import { useState, useMemo } from "react"
import { useRouter } from "next/navigation"
import { useWorkspace } from "@/providers/workspace"
import { Search, Filter } from "lucide-react"

import { useIntegrations } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

// Mock data for categories and icons - this will be replaced when backend provides this data
const providerMetadata: Record<string, { icon: string; category: string; features: string[] }> = {
  microsoft: {
    icon: "🔷",
    category: "auth",
    features: ["OAuth 2.0", "Azure AD Integration", "Microsoft Graph API", "Single Sign-On"],
  },
  google: {
    icon: "🔵",
    category: "auth",
    features: ["OAuth 2.0", "Google Workspace", "Gmail API", "Drive Integration"],
  },
  github: {
    icon: "🐙",
    category: "auth",
    features: ["Repository Access", "Automated Deployments", "Issue Tracking", "Pull Requests"],
  },
  slack: {
    icon: "💬",
    category: "communication",
    features: ["Channel Notifications", "Direct Messages", "Custom Webhooks", "Bot Integration"],
  },
  aws: {
    icon: "☁️",
    category: "cloud",
    features: ["S3 Storage", "Lambda Functions", "CloudWatch Logs", "IAM Management"],
  },
  datadog: {
    icon: "📊",
    category: "monitoring",
    features: ["Metrics Collection", "Log Aggregation", "APM Tracing", "Alerting"],
  },
  pagerduty: {
    icon: "🚨",
    category: "alerting",
    features: ["Incident Management", "On-Call Scheduling", "Alert Routing", "Escalation Policies"],
  },
  default: {
    icon: "🔌",
    category: "other",
    features: ["API Integration", "Webhook Support", "Custom Configuration"],
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

export default function IntegrationsPage() {
  const { workspaceId } = useWorkspace()
  const router = useRouter()
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedCategory, setSelectedCategory] = useState<string>("all")
  const [selectedStatus, setSelectedStatus] = useState<string>("all")

  const {
    integrations,
    integrationsIsLoading,
    providers,
    providersIsLoading,
  } = useIntegrations(workspaceId)

  // Create a map of connected integrations for quick lookup
  const connectedProviders = useMemo(() => {
    return new Set(integrations?.map(i => i.provider_id) || [])
  }, [integrations])

  // Enhanced providers with metadata
  const enhancedProviders = useMemo(() => {
    return providers?.map(provider => ({
      ...provider,
      ...getProviderInfo(provider.id),
      status: connectedProviders.has(provider.id) ? "connected" : "available",
    })) || []
  }, [providers, connectedProviders])

  // Get unique categories
  const categories = useMemo(() => {
    return Array.from(new Set(enhancedProviders.map(p => p.category)))
  }, [enhancedProviders])

  // Filter providers
  const filteredProviders = useMemo(() => {
    return enhancedProviders.filter(provider => {
      const matchesSearch =
        provider.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        provider.description?.toLowerCase().includes(searchQuery.toLowerCase())
      const matchesCategory = selectedCategory === "all" || provider.category === selectedCategory
      const matchesStatus = selectedStatus === "all" || provider.status === selectedStatus

      return matchesSearch && matchesCategory && matchesStatus
    })
  }, [enhancedProviders, searchQuery, selectedCategory, selectedStatus])

  const handleProviderClick = (providerId: string) => {
    router.push(`/workspaces/${workspaceId}/integrations/${providerId}`)
  }

  if (integrationsIsLoading || providersIsLoading) {
    return <div className="p-6">Loading integrations...</div>
  }

  return (
    <div className="container mx-auto max-w-7xl p-6">
      <div className="mb-8">
        <h1 className="mb-2 text-3xl font-bold">Integrations</h1>
        <p className="text-muted-foreground">
          Connect your workspace with powerful third-party services and tools.
        </p>
      </div>

      {/* Filters */}
      <div className="mb-6 flex flex-col gap-4 sm:flex-row">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search integrations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>
        <Select value={selectedCategory} onValueChange={setSelectedCategory}>
          <SelectTrigger className="w-full sm:w-48">
            <Filter className="mr-2 size-4" />
            <SelectValue placeholder="Category" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Categories</SelectItem>
            {categories.map(category => (
              <SelectItem key={category} value={category}>
                {category.charAt(0).toUpperCase() + category.slice(1)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={selectedStatus} onValueChange={setSelectedStatus}>
          <SelectTrigger className="w-full sm:w-48">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Status</SelectItem>
            <SelectItem value="available">Available</SelectItem>
            <SelectItem value="connected">Connected</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Stats */}
      <div className="mb-8 grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card>
          <CardContent className="p-4">
            <div className="text-2xl font-bold">{enhancedProviders.length}</div>
            <div className="text-sm text-muted-foreground">Total Integrations</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-2xl font-bold text-green-600">
              {integrations?.length || 0}
            </div>
            <div className="text-sm text-muted-foreground">Connected</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-2xl font-bold text-blue-600">{categories.length}</div>
            <div className="text-sm text-muted-foreground">Categories</div>
          </CardContent>
        </Card>
      </div>

      {/* Integrations Grid */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
        {filteredProviders.map(provider => {
          const integration = integrations?.find(i => i.provider_id === provider.id)

          return (
            <Card
              key={provider.id}
              className="cursor-pointer transition-shadow hover:shadow-lg"
              onClick={() => handleProviderClick(provider.id)}
            >
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="text-2xl">{provider.icon}</div>
                    <div>
                      <CardTitle className="text-lg">{provider.name}</CardTitle>
                      <div className="mt-1 flex gap-2">
                        <Badge className={categoryColors[provider.category]}>
                          {provider.category}
                        </Badge>
                        <Badge className={provider.status === "connected" ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-800"}>
                          {provider.status}
                        </Badge>
                      </div>
                    </div>
                  </div>
                </div>
                <CardDescription className="mt-2">
                  {provider.description || `Connect with ${provider.name} to enhance your workflows`}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div>
                    <h4 className="mb-2 text-sm font-medium">Key Features</h4>
                    <div className="flex flex-wrap gap-1">
                      {provider.features.slice(0, 3).map((feature, index) => (
                        <Badge key={index} variant="outline" className="text-xs">
                          {feature}
                        </Badge>
                      ))}
                      {provider.features.length > 3 && (
                        <Badge variant="outline" className="text-xs">
                          +{provider.features.length - 3} more
                        </Badge>
                      )}
                    </div>
                  </div>
                  {integration && (
                    <div className="text-xs text-muted-foreground">
                      Connected • {integration.token_type}
                      {integration.expires_at && (
                        <span> • Expires {new Date(integration.expires_at).toLocaleDateString()}</span>
                      )}
                    </div>
                  )}
                  <Button
                    className="w-full"
                    variant={provider.status === "connected" ? "outline" : "default"}
                    onClick={(e) => {
                      e.stopPropagation()
                      handleProviderClick(provider.id)
                    }}
                  >
                    {provider.status === "connected" ? "Manage" : "Configure"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {filteredProviders.length === 0 && (
        <div className="py-12 text-center">
          <div className="text-muted-foreground">
            No integrations found matching your criteria.
          </div>
        </div>
      )}
    </div>
  )
}
