import React, { Suspense } from "react"

import { CenteredSpinner } from "@/components/loading/spinner"
import { WorkflowsDashboard } from "@/components/playbooks/workflows-dashboard"

export default async function Page() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <WorkflowsDashboard />
    </Suspense>
  )
}
