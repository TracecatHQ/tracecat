/**
 * Inbox utilities for the Linear-style inbox UI.
 */

import type { InboxItemStatus } from "@/client"

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
