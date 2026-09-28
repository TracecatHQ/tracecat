/**
 * Inbox utilities for the Linear-style inbox UI.
 */

import type { InboxItemStatus } from "@/client"

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

/** Return a valid case UUID from Inbox URL state, or no filter. */
export function parseInboxCaseId(value: string | null): string | null {
  if (!value) {
    return null
  }
  const caseId = value.trim()
  return UUID_PATTERN.test(caseId) ? caseId : null
}

/** Build the workspace Inbox URL filtered to one case. */
export function getCaseAgentRunsHref(
  workspaceId: string,
  caseId: string
): string {
  const params = new URLSearchParams({ caseId })
  return `/workspaces/${workspaceId}/inbox?${params.toString()}`
}

/** Remove only the case filter from an Inbox URL, preserving other state. */
export function getInboxHrefWithoutCaseFilter(
  pathname: string,
  query: string
): string {
  const params = new URLSearchParams(query)
  params.delete("caseId")
  const nextQuery = params.toString()
  return nextQuery ? `${pathname}?${nextQuery}` : pathname
}

/**
 * Get display color class for status badge.
 */
export function getStatusBadgeClass(status: InboxItemStatus): string {
  switch (status) {
    case "pending":
      return "border-amber-500/50 text-amber-600"
    case "completed":
      return "border-emerald-500/50 text-emerald-600"
    case "failed":
      return "border-red-500/50 text-red-600"
    default:
      return ""
  }
}
