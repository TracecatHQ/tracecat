"use client"

import { useParams } from "next/navigation"
import { useEffect } from "react"
import { AgentPresetsBuilder } from "@/components/agents/agent-presets-builder"
import { FeatureFlagEmptyState } from "@/components/feature-flag-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"

export default function AgentPresetsPage() {
  const params = useParams<{
    presetId: string
  }>()
  const presetId = params?.presetId
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentApprovalsEnabled = isFeatureEnabled("agent-approvals")
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")
  const agentsFeatureEnabled = agentApprovalsEnabled && agentPresetsEnabled

  useEffect(() => {
    document.title = "Agent Presets"
  }, [])

  if (featureFlagsLoading) {
    return <CenteredSpinner />
  }

  if (!agentsFeatureEnabled) {
    return (
      <div className="size-full overflow-auto px-3 py-6">
        <div className="mx-auto flex w-full max-w-3xl flex-1 items-center justify-center py-12">
          <FeatureFlagEmptyState
            title="Agents unavailable"
            description="Enable both agent presets and agent approvals to manage agents from this workspace."
          />
        </div>
      </div>
    )
  }

  return (
    <div className="h-full">
      <AgentPresetsBuilder presetId={presetId} />
    </div>
  )
}
