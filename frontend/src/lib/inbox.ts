/**
 * Inbox types and utilities for the Linear-style inbox UI.
 */

import type { InboxItemRead, InboxItemStatus, InboxItemType } from "@/client"

// Re-export types from client for convenience
export type { InboxItemRead, InboxItemStatus, InboxItemType }

// Local alias for components that use InboxItem
export type InboxItem = InboxItemRead

/**
 * Sort inbox items by status priority and timestamp.
 * Pending items first, then by most recent.
 */
export function sortInboxItems(items: InboxItem[]): InboxItem[] {
  return [...items].sort((a, b) => {
    // Pending items first
    if (a.status === "pending" && b.status !== "pending") return -1
    if (b.status === "pending" && a.status !== "pending") return 1

    // Then by timestamp (most recent first)
    const aTime = new Date(a.created_at).getTime()
    const bTime = new Date(b.created_at).getTime()
    return bTime - aTime
  })
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
