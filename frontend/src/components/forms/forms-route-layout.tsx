"use client"

import { notFound, useParams } from "next/navigation"
import type { ReactNode } from "react"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { WorkspaceCollectionRouteLayout } from "@/components/workspaces/workspace-collection-route-layout"
import { useFormsAccess } from "@/hooks/use-forms-access"
import { PageContext } from "@/providers/chat-page-context"

/**
 * Shell and gate for the builder's Forms pages: the workspace collection
 * layout (sidebar, header, docked chat). Flag off: no route. No
 * entitlement: the shell with a locked state.
 */
export function FormsRouteLayout({ children }: { children: ReactNode }) {
  const access = useFormsAccess()
  const params = useParams<{ formId?: string }>()
  if (access === "hidden") {
    notFound()
  }
  if (access === "loading") {
    return <CenteredSpinner />
  }
  // On the detail page the docked chat is told which form is open. The list
  // page has no `formId`, so its chat sends no page context.
  return (
    <PageContext.Provider
      value={params?.formId ? { kind: "form", id: params.formId } : null}
    >
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
    </PageContext.Provider>
  )
}
