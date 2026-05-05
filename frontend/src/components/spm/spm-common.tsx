"use client"

import { formatDistanceToNow } from "date-fns"
import { ShieldXIcon } from "lucide-react"
import type { ReactNode } from "react"
import type {
  SpmControlRead,
  SpmEndpointRead,
  SpmFindingRead,
  SpmInventoryItemRead,
} from "@/client"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"

export type BadgeVariant = "default" | "secondary" | "destructive" | "outline"
export type FindingDecision = "dismiss" | "enforce"

export const ALL_VALUE = "all"
export const EMPTY_FILTERS = "No matching records."

export function includesQuery(
  values: Array<string | null | undefined>,
  query: string
) {
  const normalizedQuery = query.trim().toLowerCase()
  if (normalizedQuery.length === 0) {
    return true
  }
  return values.some((value) => value?.toLowerCase().includes(normalizedQuery))
}

export function formatLabel(value: string | null | undefined) {
  if (!value) {
    return "unknown"
  }
  return value
}

export function formatRelativeTimestamp(value: string | null | undefined) {
  if (!value) {
    return "never"
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return "unknown"
  }
  return formatDistanceToNow(date, { addSuffix: true })
}

export function endpointStatusVariant(
  status: SpmEndpointRead["status"]
): BadgeVariant {
  if (status === "active") {
    return "secondary"
  }
  if (status === "error" || status === "disabled") {
    return "destructive"
  }
  return "outline"
}

export function findingStatusVariant(
  status: SpmFindingRead["status"]
): BadgeVariant {
  if (status === "open") {
    return "destructive"
  }
  if (status === "enforcement_pending") {
    return "secondary"
  }
  if (status === "enforced") {
    return "default"
  }
  return "outline"
}

export function severityVariant(
  severity: SpmFindingRead["severity"] | SpmControlRead["severity"]
): BadgeVariant {
  if (severity === "critical" || severity === "high") {
    return "destructive"
  }
  if (severity === "medium") {
    return "secondary"
  }
  return "outline"
}

export function getEndpointName(
  endpointId: string,
  endpoints: SpmEndpointRead[]
) {
  return (
    endpoints.find((endpoint) => endpoint.id === endpointId)?.name ?? endpointId
  )
}

export function getInventoryItemRecord(
  inventoryItemId: string,
  inventoryItems: SpmInventoryItemRead[]
): SpmInventoryItemRead | undefined {
  return inventoryItems.find((item) => item.id === inventoryItemId)
}

export function getInventoryItemPath(item: {
  item_location?: string
  source_location: string
  identity_key: string
  metadata?: Record<string, unknown> | null
}) {
  if (item.source_location) {
    return item.source_location
  }
  if (typeof item.metadata?.file_path === "string") {
    return item.metadata.file_path
  }
  return item.identity_key
}

export function getFindingEnforcementState(finding: SpmFindingRead) {
  if (finding.status === "enforcement_pending") {
    return {
      label: "queued",
      value: formatLabel(finding.recommended_action),
      variant: "secondary" as BadgeVariant,
    }
  }
  if (finding.status === "enforced") {
    return {
      label: "applied",
      value: formatLabel(finding.recommended_action),
      variant: "default" as BadgeVariant,
    }
  }
  if (finding.source_type === "agents_md") {
    return {
      label: "inventory_only",
      value: "No Claude enforcement path",
      variant: "outline" as BadgeVariant,
    }
  }
  if (finding.recommended_action) {
    return {
      label: "ready",
      value: formatLabel(finding.recommended_action),
      variant: "outline" as BadgeVariant,
    }
  }
  if (finding.status === "dismissed" || finding.status === "resolved") {
    return {
      label: formatLabel(finding.status),
      value: "no_action_queued",
      variant: "outline" as BadgeVariant,
    }
  }
  return {
    label: "no_action",
    value: "no_enforcement_payload_available",
    variant: "outline" as BadgeVariant,
  }
}

export function getComplianceRollup(
  endpointId: string,
  findings: SpmFindingRead[]
) {
  let dismissed = 0
  let enforced = 0
  let open = 0
  let pending = 0
  let resolved = 0

  for (const finding of findings) {
    if (finding.endpoint_id !== endpointId) {
      continue
    }
    if (finding.status === "open") {
      open += 1
    } else if (finding.status === "enforcement_pending") {
      pending += 1
    } else if (finding.status === "enforced") {
      enforced += 1
    } else if (finding.status === "resolved") {
      resolved += 1
    } else if (finding.status === "dismissed") {
      dismissed += 1
    }
  }

  if (open > 0) {
    return {
      detail: `${open} open, ${pending} queued, ${enforced + resolved + dismissed} closed`,
      key: "needs_attention",
      label: "needs_attention",
      variant: "destructive" as BadgeVariant,
    }
  }
  if (pending > 0) {
    return {
      detail: `${pending} queued, ${enforced + resolved + dismissed} closed`,
      key: "enforcement_queued",
      label: "enforcement_queued",
      variant: "secondary" as BadgeVariant,
    }
  }
  if (enforced + resolved + dismissed > 0) {
    return {
      detail: `${enforced} enforced, ${resolved} resolved, ${dismissed} dismissed`,
      key: "compliant",
      label: "compliant",
      variant: "default" as BadgeVariant,
    }
  }
  return {
    detail: "No findings reported yet",
    key: "not_assessed",
    label: "not_assessed",
    variant: "outline" as BadgeVariant,
  }
}

export function canCancelPendingEnrollment(endpoint: SpmEndpointRead) {
  return (
    endpoint.status === "pending" &&
    endpoint.enrolled_at == null &&
    endpoint.last_seen_at == null &&
    endpoint.last_sync_at == null
  )
}

export function renderMaybeLoading(
  isLoading: boolean,
  hasEntitlement: boolean,
  title: string,
  description: string,
  children: ReactNode
) {
  if (isLoading) {
    return <CenteredSpinner />
  }
  if (!hasEntitlement) {
    return (
      <div className="flex size-full items-center justify-center">
        <EntitlementRequiredEmptyState
          title={title}
          description={description}
          icon={<ShieldXIcon className="h-6 w-6" />}
        />
      </div>
    )
  }
  return children
}
