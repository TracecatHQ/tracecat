"use client"

import { Search } from "lucide-react"
import { useRouter } from "next/navigation"
import { useMemo, useState } from "react"
import type { IntegrationStatus, OAuthGrantType } from "@/client"
import { ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { useIntegrations } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

// Helper function to get status display info
const getStatusInfo = (status: IntegrationStatus) => {
  switch (status) {
    case "connected":
      return {
        label: "Connected",
        className: "bg-green-50 text-green-700 border-green-200",
      }
    case "configured":
      return {
        label: "Connection incomplete",
        className: "bg-amber-50 text-amber-700 border-amber-200",
      }
    case "not_configured":
      return {
        label: "Available",
        className: "bg-gray-50 text-gray-600 border-gray-200",
      }
    default:
      return {
        label: "Available",
        className: "bg-gray-50 text-gray-600 border-gray-200",
      }
  }
}

export default function IntegrationsPage() {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const [searchQuery, setSearchQuery] = useState("")

  const { providers, providersIsLoading, providersError } =
    useIntegrations(workspaceId)

  const filteredProviders = useMemo(() => {
    const filtered = providers?.filter((provider) => {
      const { description, name } = provider
      const matchesSearch =
        name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (description ?? "").toLowerCase().includes(searchQuery.toLowerCase())

      return matchesSearch
    })

    if (!filtered) return filtered

    // Sort: Connected first, then Configured, then Available/Not Configured
    // Within each group, sort by enabled status, then alphabetically
    return [...filtered].sort((a, b) => {
      // Status priority: connected > configured > not_configured
      const statusOrder = {
        connected: 0,
        configured: 1,
        not_configured: 2,
      }

      const aOrder = statusOrder[a.integration_status] ?? 2
      const bOrder = statusOrder[b.integration_status] ?? 2

      if (aOrder !== bOrder) {
        return aOrder - bOrder
      }

      // Within same status, sort by enabled first
      if (a.enabled !== b.enabled) {
        return a.enabled ? -1 : 1
      }

      // Finally, sort alphabetically
      return a.name.localeCompare(b.name)
    })
  }, [providers, searchQuery])

  const handleProviderClick = ({
    id,
    enabled,
    grantType,
  }: {
    id: string
    enabled: boolean
    grantType: OAuthGrantType
  }) => {
    if (enabled) {
      router.push(
        `/workspaces/${workspaceId}/integrations/${id}?tab=overview&grant_type=${grantType}`
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
    <div className="flex flex-col min-h-0 max-w-5xl mx-auto my-16 px-8">
      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <Input
            placeholder="Search integrations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-9 border-gray-300 bg-gray-50 pl-10 text-sm focus:border-gray-400 focus:bg-white"
          />
        </div>
      </div>

      {/* Integrations List */}
      <div className="space-y-3">
        {filteredProviders?.map((provider) => {
          const {
            id,
            enabled,
            name,
            description,
            grant_type: grantType,
          } = provider
          const statusInfo = getStatusInfo(provider.integration_status)

          return (
            <Card
              key={`${id}-${grantType}`}
              className={cn(
                "border-gray-200 p-4 transition-colors",
                enabled
                  ? "cursor-pointer hover:bg-gray-50"
                  : "cursor-not-allowed opacity-60"
              )}
              onClick={() =>
                handleProviderClick({
                  id,
                  enabled,
                  grantType,
                })
              }
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <ProviderIcon
                    providerId={id}
                    className="h-8 w-8 rounded-md p-1.5"
                  />
                  <div className="flex-1">
                    <h3 className="text-sm font-medium text-gray-900">
                      {name}
                    </h3>
                    <p className="mt-1 text-xs text-gray-500">
                      {description ||
                        `Connect with ${name} to enhance your workflows`}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  {enabled && (
                    <>
                      <span
                        className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          provider.integration_status === "connected" &&
                            "bg-green-500",
                          provider.integration_status === "configured" &&
                            "bg-yellow-500",
                          provider.integration_status === "not_configured" &&
                            "bg-gray-400"
                        )}
                      />
                      <span className="text-xs text-gray-500">
                        {statusInfo.label}
                      </span>
                    </>
                  )}
                  {!enabled && (
                    <span className="text-xs text-gray-500">Coming soon</span>
                  )}
                </div>
              </div>
            </Card>
          )
        })}
      </div>
      {filteredProviders?.length === 0 && (
        <Card className="border-gray-200">
          <div className="py-12 text-center">
            <p className="text-sm text-gray-500">
              No integrations found matching your criteria.
            </p>
          </div>
        </Card>
      )}
    </div>
  )
}
