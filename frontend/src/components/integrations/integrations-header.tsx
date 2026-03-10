"use client"

import {
  Link2,
  Lock,
  LockKeyhole,
  Sparkles,
  Unlink2,
  WrenchIcon,
} from "lucide-react"
import {
  CatalogHeader,
  type CatalogHeaderPillOption,
  type CatalogHeaderSelectFilter,
} from "@/components/catalog/catalog-header"

export type IntegrationTypeFilter =
  | "oauth"
  | "custom_oauth"
  | "mcp"
  | "custom_mcp"
export type ConnectionFilter = "all" | "connected" | "not_connected"

interface IntegrationsHeaderProps {
  searchQuery: string
  onSearchChange: (query: string) => void
  typeFilters: IntegrationTypeFilter[]
  onTypeFilterToggle: (filter: IntegrationTypeFilter) => void
  connectionFilter: ConnectionFilter
  onConnectionFilterChange: (filter: ConnectionFilter) => void
  displayIntegrationCount?: number
}

const TYPE_FILTER_OPTIONS: Array<
  CatalogHeaderPillOption<IntegrationTypeFilter>
> = [
  { value: "oauth", label: "OAuth", icon: Lock },
  { value: "custom_oauth", label: "Custom OAuth", icon: LockKeyhole },
  { value: "mcp", label: "MCP", icon: Sparkles },
  { value: "custom_mcp", label: "Custom MCP", icon: WrenchIcon },
]

export function IntegrationsHeader({
  searchQuery,
  onSearchChange,
  typeFilters,
  onTypeFilterToggle,
  connectionFilter,
  onConnectionFilterChange,
  displayIntegrationCount = 0,
}: IntegrationsHeaderProps) {
  const selectFilters: CatalogHeaderSelectFilter[] = [
    {
      key: "connection",
      value: connectionFilter,
      onValueChange: (value) =>
        onConnectionFilterChange(value as ConnectionFilter),
      placeholder: "Connection",
      allValue: "all",
      options: [
        { value: "all", label: "All connections" },
        { value: "connected", label: "Connected", icon: Link2 },
        {
          value: "not_connected",
          label: "Not connected",
          icon: Unlink2,
        },
      ],
    },
  ]

  return (
    <CatalogHeader
      searchQuery={searchQuery}
      onSearchChange={onSearchChange}
      searchPlaceholder="Search integrations..."
      pillFilters={TYPE_FILTER_OPTIONS}
      activePillFilters={typeFilters}
      onPillFilterToggle={onTypeFilterToggle}
      selectFilters={selectFilters}
      displayCount={displayIntegrationCount}
      countLabel="integrations"
    />
  )
}
