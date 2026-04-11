"use client"

import { useEffect } from "react"
import { ActivityLayout } from "@/components/approvals"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useApprovals } from "@/hooks/use-approvals"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function ApprovalsPage() {
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const canReadApprovals = useScopeCheck("approval:read")

  const {
    sessions,
    selectedId,
    setSelectedId,
    isLoading: approvalsIsLoading,
    error: approvalsError,
    filters,
    setSearchQuery,
    setEntityType,
    setLimit,
    setUpdatedAfter,
    setCreatedAfter,
  } = useApprovals({ enabled: agentAddonsEnabled && canReadApprovals })

  useEffect(() => {
    document.title = "Approvals"
  }, [])

  if (entitlementsLoading) {
    return <CenteredSpinner />
  }

  if (!canReadApprovals) {
    return null
  }

  if (!agentAddonsEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="mx-auto flex h-full w-full max-w-3xl flex-1 items-center justify-center py-12">
          <EntitlementRequiredEmptyState
            title="Enterprise only"
            description="Advanced AI agents (human-in-the-loop and subagents) are only available on enterprise plans."
          />
        </div>
      </div>
    )
  }

  return (
    <ActivityLayout
      sessions={sessions}
      selectedId={selectedId}
      onSelect={setSelectedId}
      isLoading={approvalsIsLoading}
      error={approvalsError ?? null}
      filters={filters}
      onSearchChange={setSearchQuery}
      onEntityTypeChange={setEntityType}
      onLimitChange={setLimit}
      onUpdatedAfterChange={setUpdatedAfter}
      onCreatedAfterChange={setCreatedAfter}
    />
  )
}
