"use client"

import { useParams } from "next/navigation"
import type React from "react"
import { WorkspaceCollectionRouteLayout } from "@/components/workspaces/workspace-collection-route-layout"

export function TablesRouteLayout({ children }: { children: React.ReactNode }) {
  const params = useParams<{ tableId?: string }>()

  return (
    <WorkspaceCollectionRouteLayout detailId={params?.tableId}>
      {children}
    </WorkspaceCollectionRouteLayout>
  )
}
