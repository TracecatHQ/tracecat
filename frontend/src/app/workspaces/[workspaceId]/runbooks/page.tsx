import { Suspense } from "react"
import { RunbooksDashboard } from "@/components/dashboard/runbooks-dashboard"
import { CenteredSpinner } from "@/components/loading/spinner"

export default async function RunbooksDashboardPage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <RunbooksDashboard />
    </Suspense>
  )
}
