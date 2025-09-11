"use client"

import { Suspense, useEffect } from "react"
import { RunbooksDashboard } from "@/components/dashboard/runbooks-dashboard"
import { CenteredSpinner } from "@/components/loading/spinner"

export default function RunbooksDashboardPage() {
  useEffect(() => {
    document.title = "Runbooks"
  }, [])

  return (
    <Suspense fallback={<CenteredSpinner />}>
      <RunbooksDashboard />
    </Suspense>
  )
}
