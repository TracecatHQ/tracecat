"use client"

import { useEffect, useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { ActivityLayout } from "@/components/inbox"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useEntitlements } from "@/hooks/use-entitlements"
import { type InboxOrderBy, useInbox } from "@/hooks/use-inbox"

export default function InboxPage() {
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const canReadInbox = useScopeCheck("inbox:read")

  // Sort is applied server-side so it orders every page of a group globally,
  // not just the rows already loaded in the browser.
  const [orderBy, setOrderBy] = useState<InboxOrderBy>("updated_at")
  const [sort, setSort] = useState<"asc" | "desc">("desc")

  const {
    sessions,
    groups,
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
  } = useInbox({
    enabled: agentAddonsEnabled && canReadInbox,
    orderBy,
    sort,
  })

  const handleSort = (key: InboxOrderBy) => {
    if (key === orderBy) {
      setSort((prev) => (prev === "asc" ? "desc" : "asc"))
    } else {
      setOrderBy(key)
      setSort("desc")
    }
  }

  useEffect(() => {
    document.title = "Inbox"
  }, [])

  if (entitlementsLoading) {
    return <CenteredSpinner />
  }

  if (!canReadInbox) {
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
      groups={groups}
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
      orderBy={orderBy}
      sort={sort}
      onSort={handleSort}
    />
  )
}
