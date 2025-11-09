"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { AgentsBoard } from "@/components/agents/agents-dashboard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useAgentSessions } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function AgentsPage() {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentFeatureEnabled = isFeatureEnabled("agent-approvals")

  const { sessions, sessionsIsLoading, sessionsError, refetchSessions } =
    useAgentSessions({ workspaceId }, { enabled: agentFeatureEnabled })

  useEffect(() => {
    document.title = "Agents"
  }, [])

  useEffect(() => {
    if (!featureFlagsLoading && !agentFeatureEnabled) {
      router.replace("/not-found")
    }
  }, [agentFeatureEnabled, featureFlagsLoading, router])

  if (featureFlagsLoading || !agentFeatureEnabled) {
    return <CenteredSpinner />
  }

  return (
    <div className="size-full overflow-auto px-3 py-6">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
        <header className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
          <p className="text-sm text-muted-foreground">
            Monitor agent runs and approvals grouped by their latest status.
          </p>
        </header>
        <AgentsBoard
          sessions={sessions}
          isLoading={sessionsIsLoading}
          error={sessionsError ?? null}
          onRetry={() => {
            void refetchSessions()
          }}
        />
      </div>
    </div>
  )
}
