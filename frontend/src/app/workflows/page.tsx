import React, { Suspense } from "react"

import { WorkflowsDashboard } from "@/components/dashboard/workflows-dashboard"
import { CenteredSpinner } from "@/components/loading/spinner"

export default async function Page() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <WorkflowsDashboard />
    </Suspense>
  )
}
