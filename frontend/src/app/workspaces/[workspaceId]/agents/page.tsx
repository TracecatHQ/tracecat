import { Suspense } from "react"
import { AgentsDashboard } from "@/components/agents/agents-dashboard"
import { CenteredSpinner } from "@/components/loading/spinner"

export default function AgentsPage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <AgentsDashboard />
    </Suspense>
  )
}
