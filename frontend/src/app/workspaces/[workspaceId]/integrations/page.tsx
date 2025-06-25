"use client"

import { Filter, Search } from "lucide-react"
import { useRouter } from "next/navigation"
import { useMemo, useState } from "react"
import {
  $ProviderCategory,
  type IntegrationStatus,
  type ProviderCategory,
} from "@/client"
import { ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useIntegrations } from "@/lib/hooks"
import { categoryColors } from "@/lib/provider-styles"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

// Helper function to get status display info
const getStatusInfo = (status: IntegrationStatus) => {
  switch (status) {
    case "connected":
      return {
        label: "Connected",
        className: "bg-green-100 text-green-800 hover:bg-green-200",
      }
    case "configured":
      return {
        label: "Configured",
        className: "bg-yellow-100 text-yellow-800 hover:bg-yellow-200",
      }
    default:
      return {
        label: "Available",
        className: "bg-gray-100 text-gray-800 hover:bg-gray-200",
      }
  }
}
const categories = Object.values($ProviderCategory.enum) as ProviderCategory[]

export default function IntegrationsPage() {
  const { workspaceId } = useWorkspace()
  const router = useRouter()
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedCategory, setSelectedCategory] =
    useState<ProviderCategory | null>(null)
  const [selectedStatus, setSelectedStatus] =
    useState<IntegrationStatus | null>(null)

  const { providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)

  const filteredProviders = useMemo(() => {
    return providers?.filter((provider) => {
      const metadata = provider.metadata
      const matchesSearch =
        metadata.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (metadata.description ?? "")
          .toLowerCase()
          .includes(searchQuery.toLowerCase())
      const matchesCategory =
        selectedCategory === null ||
        (metadata.categories && metadata.categories.includes(selectedCategory))
      const matchesStatus =
        selectedStatus === null ||
        provider.integration_status === selectedStatus

      return matchesSearch && matchesCategory && matchesStatus
    })
  }, [providers, searchQuery, selectedCategory, selectedStatus])

  const handleProviderClick = (providerId: string, enabled: boolean) => {
    if (enabled) {
      router.push(`/workspaces/${workspaceId}/integrations/${providerId}`)
    }
  }

  if (providersIsLoading) {
    return <CenteredSpinner />
  }
  if (providersError) {
    return <div>Error: {providersError.message}</div>
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
        <Select
          value={selectedCategory ?? "all"}
          onValueChange={(value) =>
            setSelectedCategory(value as ProviderCategory)
          }
        >
          <SelectTrigger className="w-full sm:w-48">
            <Filter className="mr-2 size-4" />
            <SelectValue placeholder="Category" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Categories</SelectItem>
            {categories.map((category) => (
              <SelectItem key={category} value={category}>
                {category.charAt(0).toUpperCase() + category.slice(1)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={selectedStatus ?? "all"}
          onValueChange={(value) =>
            setSelectedStatus(value as IntegrationStatus)
          }
        >
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
            <div className="text-2xl font-bold">{providers?.length || 0}</div>
            <div className="text-sm text-muted-foreground">
              Total Integrations
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-2xl font-bold text-green-600">
              {providers?.filter((p) => p.integration_status === "connected")
                .length || 0}
            </div>
            <div className="text-sm text-muted-foreground">Connected</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-2xl font-bold text-blue-600">
              {categories.length}
            </div>
            <div className="text-sm text-muted-foreground">Categories</div>
          </CardContent>
        </Card>
      </div>

      {/* Integrations Grid */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
        {filteredProviders?.map((provider) => {
          const metadata = provider.metadata
          const statusInfo = getStatusInfo(provider.integration_status)
          const isEnabled = metadata.enabled !== false

          return (
            <Card
              key={metadata.id}
              className={cn(
                isEnabled
                  ? "cursor-pointer transition-colors duration-200 hover:bg-accent/50"
                  : "cursor-not-allowed opacity-50"
              )}
              onClick={() => handleProviderClick(metadata.id, isEnabled)}
            >
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-3">
                    <ProviderIcon
                      providerId={metadata.id}
                      className="size-8 p-1.5"
                    />
                    <div>
                      <CardTitle className="text-lg">{metadata.name}</CardTitle>
                      <div className="mt-1 flex gap-2">
                        {metadata.categories?.map((category, index) => (
                          <Badge
                            key={index}
                            className={cn(
                              categoryColors[category] || categoryColors.other,
                              "whitespace-nowrap"
                            )}
                          >
                            {category}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </div>
                  <Badge
                    className={cn(
                      isEnabled
                        ? statusInfo.className
                        : "bg-orange-100 text-orange-800 hover:bg-orange-200",
                      "whitespace-nowrap"
                    )}
                  >
                    {isEnabled ? statusInfo.label : "Coming Soon"}
                  </Badge>
                </div>
                <CardDescription className="mt-2">
                  {metadata.description ||
                    `Connect with ${metadata.name} to enhance your workflows`}
                </CardDescription>
              </CardHeader>
            </Card>
          )
        })}
      </div>

      {filteredProviders?.length === 0 && (
        <div className="py-12 text-center">
          <div className="text-muted-foreground">
            No integrations found matching your criteria.
          </div>
        </div>
      )}
    </div>
  )
}
