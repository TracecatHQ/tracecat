import { Suspense } from "react"
import { AgendasDashboard } from "@/components/dashboard/agendas-dashboard"
import { CenteredSpinner } from "@/components/loading/spinner"

export default async function AgendasDashboardPage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <AgendasDashboard />
    </Suspense>
  )
}
