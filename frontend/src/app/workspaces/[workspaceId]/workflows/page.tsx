"use client"

import { Suspense } from "react"
import { WorkflowsDashboard } from "@/components/dashboard/workflows-dashboard"
import { CenteredSpinner } from "@/components/loading/spinner"

export default function WorkflowsDashboardPage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <WorkflowsDashboard />
    </Suspense>
  )
}
