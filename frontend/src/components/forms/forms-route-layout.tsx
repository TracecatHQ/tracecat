"use client"

import { notFound } from "next/navigation"
import type { ReactNode } from "react"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { WorkspaceCollectionRouteLayout } from "@/components/workspaces/workspace-collection-route-layout"
import { useFormsAccess } from "@/hooks/use-forms-access"

/**
 * Shell and gate for the builder's Forms pages, list and detail alike.
 *
 * Both pages get the workspace sidebar, the header, the chat toggle and the
 * docked chat from `WorkspaceCollectionRouteLayout`. With the feature flag
 * off the routes do not exist. With the flag on and no entitlement, the
 * shell renders and the page is replaced by a locked state.
 */
export function FormsRouteLayout({ children }: { children: ReactNode }) {
  const access = useFormsAccess()
  if (access === "hidden") {
    notFound()
  }
  if (access === "loading") {
    return <CenteredSpinner />
  }
  return (
    <WorkspaceCollectionRouteLayout>
      {access === "locked" ? (
        <div className="flex h-full items-center justify-center p-8">
          <EntitlementRequiredEmptyState
            title="Forms aren't available"
            description="Forms are not included in your organization's plan. Contact your administrator."
          />
        </div>
      ) : (
        children
      )}
    </WorkspaceCollectionRouteLayout>
  )
}
