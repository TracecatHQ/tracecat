"use client"

import {
  ArrowUpDown,
  Filter,
  Key,
  type LucideIcon,
  Search,
  User2Icon,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { useMemo, useState } from "react"
import {
  $ProviderCategory,
  type IntegrationStatus,
  type OAuthGrantType,
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
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
const grantTypeStyles: Record<
  OAuthGrantType,
  {
    icon: LucideIcon
    label: string
  }
> = {
  authorization_code: {
    icon: User2Icon,
    label: "Authorization Code",
  },
  client_credentials: {
    icon: Key,
    label: "Client Credentials",
  },
}

export default function IntegrationsPage() {
  const { workspaceId } = useWorkspace()
  const router = useRouter()
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedCategory, setSelectedCategory] =
    useState<ProviderCategory | null>(null)
  const [selectedStatus, setSelectedStatus] =
    useState<IntegrationStatus | null>(null)
  const [sortBy, setSortBy] = useState<"default" | "name" | "availability">(
    "default"
  )

  const { providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)

  const filteredProviders = useMemo(() => {
    const filtered = providers?.filter((provider) => {
      const { description, name, categories: providerCategories } = provider
      const matchesSearch =
        name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (description ?? "").toLowerCase().includes(searchQuery.toLowerCase())
      const matchesCategory =
        selectedCategory === null ||
        (providerCategories && providerCategories.includes(selectedCategory))
      const matchesStatus =
        selectedStatus === null ||
        provider.integration_status === selectedStatus

      return matchesSearch && matchesCategory && matchesStatus
    })

    if (!filtered) return filtered

    // Sort the filtered results
    return [...filtered].sort((a, b) => {
      if (sortBy === "availability") {
        // First sort by enabled status (enabled first)
        if (a.enabled !== b.enabled) {
          return a.enabled ? -1 : 1
        }
        // Then by name alphabetically
        return a.name.localeCompare(b.name)
      } else if (sortBy === "name") {
        return a.name.localeCompare(b.name)
      } else {
        // Default: availability first, then alphabetical
        if (a.enabled !== b.enabled) {
          return a.enabled ? -1 : 1
        }
        return a.name.localeCompare(b.name)
      }
    })
  }, [providers, searchQuery, selectedCategory, selectedStatus, sortBy])

  const handleProviderClick = (providerId: string, enabled: boolean) => {
    if (enabled) {
      router.push(
        `/workspaces/${workspaceId}/integrations/${providerId}?tab=overview`
      )
    }
  }

  if (providersIsLoading) {
    return <CenteredSpinner />
  }
  if (providersError) {
    return <div>Error: {providersError.message}</div>
  }

  return (
    <div className="size-full overflow-auto">
      <div className="flex size-full flex-col space-y-12">
        <div className="flex w-full items-center justify-between">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Integrations
            </h2>
            <p className="text-md text-muted-foreground">
              Connect your workspace with third-party services and tools.
            </p>
          </div>
        </div>

        <>
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
            <Select
              value={sortBy}
              onValueChange={(value) =>
                setSortBy(value as "default" | "name" | "availability")
              }
            >
              <SelectTrigger className="w-full sm:w-48">
                <ArrowUpDown className="mr-2 size-4" />
                <SelectValue placeholder="Sort by" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">Default</SelectItem>
                <SelectItem value="availability">Availability</SelectItem>
                <SelectItem value="name">Name (A-Z)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Stats */}
          <div className="mb-8 grid grid-cols-1 gap-4 md:grid-cols-3">
            <Card>
              <CardContent className="p-4">
                <div className="text-2xl font-bold">
                  {providers?.length || 0}
                </div>
                <div className="text-sm text-muted-foreground">
                  Total Integrations
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-4">
                <div className="text-2xl font-bold text-green-600">
                  {providers?.filter(
                    (p) => p.integration_status === "connected"
                  ).length || 0}
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
              const {
                id,
                enabled,
                name,
                description,
                categories: providerCategories,
              } = provider
              const statusInfo = getStatusInfo(provider.integration_status)

              const { icon: Icon, label } = grantTypeStyles[provider.grant_type]
              return (
                <Card
                  key={id}
                  className={cn(
                    !!enabled
                      ? "cursor-pointer transition-colors duration-200 hover:bg-accent/50"
                      : "cursor-not-allowed opacity-50"
                  )}
                  onClick={() => handleProviderClick(id, enabled)}
                >
                  <CardHeader className="flex flex-col gap-1">
                    <div className="flex items-start justify-between">
                      <div className="flex items-start gap-3">
                        <ProviderIcon
                          providerId={id}
                          className="size-8 p-1.5"
                        />
                        <div>
                          <div className="flex items-center gap-4">
                            <CardTitle className="text-lg">{name}</CardTitle>
                          </div>
                          <div className="mt-1 flex items-center gap-2">
                            {providerCategories?.map((category, index) => (
                              <Badge
                                key={index}
                                className={cn(
                                  "!shadow-none whitespace-nowrap capitalize",
                                  categoryColors[category] ||
                                    categoryColors.other
                                )}
                              >
                                {category}
                              </Badge>
                            ))}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="shrink-0 rounded-md bg-muted p-1">
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Icon
                                  className="size-4 text-muted-foreground/70"
                                  strokeWidth={2.5}
                                />
                              </TooltipTrigger>
                              <TooltipContent>
                                <p>{label}</p>
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        </div>
                        <Badge
                          className={cn(
                            enabled
                              ? statusInfo.className
                              : "bg-orange-100 text-orange-800 hover:bg-orange-200",
                            "!shadow-none whitespace-nowrap"
                          )}
                        >
                          {enabled ? statusInfo.label : "Coming Soon"}
                        </Badge>
                      </div>
                    </div>
                    <CardDescription className="text-xs mt-2">
                      {description ||
                        `Connect with ${name} to enhance your workflows`}
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
        </>
      </div>
    </div>
  )
}
