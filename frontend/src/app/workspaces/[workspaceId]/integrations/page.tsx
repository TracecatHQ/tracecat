"use client"

import { Star, UserSquare } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useMemo, useState } from "react"
import type { CatalogIntegrationRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  CatalogHeader,
  type CatalogHeaderPillOption,
} from "@/components/catalog/catalog-header"
import { CatalogCard } from "@/components/integrations/catalog-card"
import { IntegrationDetailPanel } from "@/components/integrations/integration-detail-panel"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { useListIntegrationCatalog } from "@/lib/hooks/integrations-catalog"
import { useWorkspaceId } from "@/providers/workspace-id"

type CatalogPillFilter = "built_by_me"
type CatalogCardIntent = "connect" | "configure" | "open"

const PILL_FILTERS: Array<CatalogHeaderPillOption<CatalogPillFilter>> = [
  { value: "built_by_me", label: "Built by me", icon: UserSquare },
]

const POPULAR_NAMESPACES = new Set([
  "github",
  "slack",
  "google_gmail",
  "google_drive",
  "microsoft_graph",
  "servicenow",
])

const INTEGRATION_ID_PARAM = "integrationId"

function matchesFilters(
  integration: CatalogIntegrationRead,
  activeFilters: CatalogPillFilter[]
): boolean {
  if (activeFilters.length === 0) return true
  return activeFilters.every((filter) => {
    switch (filter) {
      case "built_by_me":
        return integration.source === "workspace"
      default:
        return true
    }
  })
}

function catalogCardState(integration: CatalogIntegrationRead): {
  ctaIntent: CatalogCardIntent
  isConnected: boolean
} {
  const authOptions = integration.auth_options ?? []
  const isConnected = authOptions.some(
    (option) => option.status === "connected"
  )
  const needsConfiguration = authOptions.some(
    (option) =>
      option.requires_config === true && option.status === "not_configured"
  )
  const hasReadyConnectableOption = authOptions.some(
    (option) =>
      option.enabled !== false &&
      (option.auth_method === "static_kv" ||
        (option.auth_method === "oauth_auth_code" &&
          !(
            option.requires_config === true &&
            option.status === "not_configured"
          )))
  )

  if (!isConnected && hasReadyConnectableOption) {
    return { ctaIntent: "connect", isConnected }
  }
  if (needsConfiguration) {
    return {
      ctaIntent: "configure",
      isConnected,
    }
  }
  return { ctaIntent: "open", isConnected }
}

export default function IntegrationsPage() {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const searchParams = useSearchParams()
  const canRead = useScopeCheck("integration:read")

  const [searchQuery, setSearchQuery] = useState("")
  const [activeFilters, setActiveFilters] = useState<CatalogPillFilter[]>([])

  const { catalog, catalogIsLoading, catalogError } = useListIntegrationCatalog(
    workspaceId,
    {
      search: searchQuery || null,
    }
  )

  const togglePillFilter = useCallback((filter: CatalogPillFilter) => {
    setActiveFilters((prev) =>
      prev.includes(filter)
        ? prev.filter((value) => value !== filter)
        : [...prev, filter]
    )
  }, [])

  const filteredCatalog = useMemo(() => {
    if (!catalog) return []
    return catalog
      .filter((integration) => matchesFilters(integration, activeFilters))
      .sort((a, b) => a.display_name.localeCompare(b.display_name))
  }, [catalog, activeFilters])

  const { popular, rest } = useMemo(() => {
    const showPopular = searchQuery.trim() === "" && activeFilters.length === 0
    if (!showPopular) {
      return { popular: [] as CatalogIntegrationRead[], rest: filteredCatalog }
    }
    const popularList: CatalogIntegrationRead[] = []
    const restList: CatalogIntegrationRead[] = []
    for (const integration of filteredCatalog) {
      if (POPULAR_NAMESPACES.has(integration.namespace)) {
        popularList.push(integration)
      } else {
        restList.push(integration)
      }
    }
    return { popular: popularList, rest: restList }
  }, [filteredCatalog, searchQuery, activeFilters])

  const selectedIntegrationId = searchParams?.get(INTEGRATION_ID_PARAM) ?? null

  const updateSelection = useCallback(
    (next: string | null) => {
      const params = new URLSearchParams(searchParams?.toString() || "")
      if (next) {
        params.set(INTEGRATION_ID_PARAM, next)
      } else {
        params.delete(INTEGRATION_ID_PARAM)
      }
      const query = params.toString()
      router.replace(
        `/workspaces/${workspaceId}/integrations${query ? `?${query}` : ""}`
      )
    },
    [router, searchParams, workspaceId]
  )

  if (canRead !== true) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <Alert className="max-w-md">
          <AlertTitle>Access denied</AlertTitle>
          <AlertDescription>
            You do not have permission to view integrations.
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <CatalogHeader<CatalogPillFilter>
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="Search integrations..."
        pillFilters={PILL_FILTERS}
        activePillFilters={activeFilters}
        onPillFilterToggle={togglePillFilter}
        displayCount={filteredCatalog.length}
        countLabel="integrations"
      />

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto flex max-w-6xl flex-col gap-6">
          <p className="text-sm text-muted-foreground">
            Browse and connect integrations available to this workspace.
          </p>

          {catalogError ? (
            <Alert variant="destructive">
              <AlertTitle>Failed to load integrations</AlertTitle>
              <AlertDescription>
                {String(catalogError.body?.detail ?? catalogError.message)}
              </AlertDescription>
            </Alert>
          ) : null}

          {catalogIsLoading ? (
            <CenteredSpinner />
          ) : (
            <>
              {popular.length > 0 ? (
                <section className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Star className="size-4 text-amber-500" />
                    <h2 className="text-sm font-semibold text-foreground">
                      Popular in workspace
                    </h2>
                  </div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {popular.map((integration) => {
                      const cardState = catalogCardState(integration)
                      return (
                        <CatalogCard
                          key={integration.id}
                          integration={integration}
                          ctaIntent={cardState.ctaIntent}
                          isConnected={cardState.isConnected}
                          onSelect={() => updateSelection(integration.id)}
                        />
                      )
                    })}
                  </div>
                </section>
              ) : null}

              <section className="space-y-3">
                {popular.length > 0 ? (
                  <h2 className="text-sm font-semibold text-foreground">
                    All integrations
                  </h2>
                ) : null}
                {rest.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No integrations match your filters.
                  </p>
                ) : (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {rest.map((integration) => {
                      const cardState = catalogCardState(integration)
                      return (
                        <CatalogCard
                          key={integration.id}
                          integration={integration}
                          ctaIntent={cardState.ctaIntent}
                          isConnected={cardState.isConnected}
                          onSelect={() => updateSelection(integration.id)}
                        />
                      )
                    })}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </div>

      <IntegrationDetailPanel
        workspaceId={workspaceId}
        integrationId={selectedIntegrationId}
        onClose={() => updateSelection(null)}
      />
    </div>
  )
}
