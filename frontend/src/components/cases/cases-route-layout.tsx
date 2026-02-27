"use client"

import { useParams } from "next/navigation"
import type React from "react"
import { CaseSelectionProvider } from "@/components/cases/case-selection-context"
import { WorkspaceCollectionRouteLayout } from "@/components/workspaces/workspace-collection-route-layout"

export function CasesRouteLayout({ children }: { children: React.ReactNode }) {
  const params = useParams<{ caseId?: string }>()

  return (
    <WorkspaceCollectionRouteLayout
      detailId={params?.caseId}
      wrapMainContent={(content) => (
        <CaseSelectionProvider>{content}</CaseSelectionProvider>
      )}
    >
      {children}
    </WorkspaceCollectionRouteLayout>
  )
}
