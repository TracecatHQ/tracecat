"use client"

import { useEffect } from "react"
import { AgentsBoard } from "@/components/agents/agents-dashboard"
import { FeatureFlagEmptyState } from "@/components/feature-flag-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useAgentSessions } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function ApprovalsPage() {
  const workspaceId = useWorkspaceId()
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentApprovalsEnabled = isFeatureEnabled("agent-approvals")
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")
  const agentsFeatureEnabled = agentApprovalsEnabled && agentPresetsEnabled

  const { sessions, sessionsIsLoading, sessionsError, refetchSessions } =
    useAgentSessions({ workspaceId }, { enabled: agentsFeatureEnabled })

  useEffect(() => {
    document.title = "Approvals"
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
      <div className="mx-auto flex w-full max-w-5xl flex-col items-center gap-6">
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
