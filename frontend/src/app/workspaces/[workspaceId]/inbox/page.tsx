"use client"

import { useEffect } from "react"
import { FeatureFlagEmptyState } from "@/components/feature-flag-empty-state"
import { ActivityLayout } from "@/components/inbox"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useInbox } from "@/hooks/use-inbox"

export default function InboxPage() {
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  const {
    sessions,
    selectedId,
    setSelectedId,
    isLoading: inboxIsLoading,
    error: inboxError,
    filters,
    setSearchQuery,
    setEntityType,
    setLimit,
    setUpdatedAfter,
    setCreatedAfter,
  } = useInbox({ enabled: agentAddonsEnabled })

  useEffect(() => {
    document.title = "Inbox"
  }, [])

  if (entitlementsLoading) {
    return <CenteredSpinner />
  }

  if (!agentAddonsEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="mx-auto flex h-full w-full max-w-3xl flex-1 items-center justify-center py-12">
          <FeatureFlagEmptyState
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
      isLoading={inboxIsLoading}
      error={inboxError ?? null}
      filters={filters}
      onSearchChange={setSearchQuery}
      onEntityTypeChange={setEntityType}
      onLimitChange={setLimit}
      onUpdatedAfterChange={setUpdatedAfter}
      onCreatedAfterChange={setCreatedAfter}
    />
  )
}
