"use client"

import { useEffect } from "react"
import { AgentsBoard } from "@/components/agents/agents-dashboard"
import { FeatureFlagEmptyState } from "@/components/feature-flag-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useAgentSessions } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function AgentsPage() {
  const workspaceId = useWorkspaceId()
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentApprovalsEnabled = isFeatureEnabled("agent-approvals")
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")
  const agentsFeatureEnabled = agentApprovalsEnabled && agentPresetsEnabled

  const { sessions, sessionsIsLoading, sessionsError, refetchSessions } =
    useAgentSessions({ workspaceId }, { enabled: agentsFeatureEnabled })

  useEffect(() => {
    document.title = "Agents"
  }, [])

  if (featureFlagsLoading) {
    return <CenteredSpinner />
  }

  if (!agentsFeatureEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="mx-auto flex w-full h-full max-w-3xl flex-1 items-center justify-center py-12">
          <FeatureFlagEmptyState
            title="Enterprise only"
            description="Advanced AI agents (human-in-the-loop and subagents) are only available on enterprise plans."
          />
        </div>
      </div>
    )
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
