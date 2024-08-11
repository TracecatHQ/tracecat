import React, { Suspense } from "react"

import { CenteredSpinner } from "@/components/loading/spinner"
import { WorkspacesDashboard } from "@/components/workspaces/workspaces-dashboard"

export default async function WorkspacesPage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <WorkspacesDashboard />
    </Suspense>
  )
}
