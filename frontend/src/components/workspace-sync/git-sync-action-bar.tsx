"use client"

import type { ReactNode } from "react"

/**
 * Sticky controls footer for the Git Sync page: the target, the inputs, and
 * the action sit together in one full-width row.
 */
export function GitSyncActionBar({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="sticky bottom-0 z-10 mt-auto flex min-w-0 flex-wrap items-center gap-2 border-t bg-background px-5 py-3"
    >
      {children}
    </div>
  )
}
