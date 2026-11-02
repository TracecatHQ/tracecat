"use client"

import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useEffect, useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ActivityLayout } from "@/components/inbox"
import { useEntitlements } from "@/hooks/use-entitlements"
import { type InboxOrderBy, useInbox } from "@/hooks/use-inbox"
import { getInboxHrefWithoutCaseFilter, parseInboxCaseId } from "@/lib/inbox"

export default function InboxPage() {
  const canReadInbox = useScopeCheck("inbox:read")
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const requestedCaseId = parseInboxCaseId(searchParams?.get("caseId") ?? null)
  // Case-filtered agent runs require case add-ons; ignore the param otherwise.
  const caseId =
    requestedCaseId && hasEntitlement("case_addons") ? requestedCaseId : null

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
    enabled:
      canReadInbox === true &&
      (requestedCaseId === null || !entitlementsLoading),
    caseId,
    orderBy,
    sort,
  })

  const clearCaseFilter = () => {
    router.replace(
      getInboxHrefWithoutCaseFilter(pathname, searchParams?.toString() ?? "")
    )
  }

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

  if (!canReadInbox) {
    return null
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
      caseId={caseId}
      onClearCaseFilter={clearCaseFilter}
      orderBy={orderBy}
      sort={sort}
      onSort={handleSort}
    />
  )
}
