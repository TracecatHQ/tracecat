"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { AgentPresetsBuilder } from "@/components/agents/agent-presets-builder"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"

export default function AgentPresetsPage() {
  const router = useRouter()
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentPresetsEnabled = isFeatureEnabled("agent-approvals")

  useEffect(() => {
    document.title = "Agent Presets"
  }, [])

  useEffect(() => {
    if (!featureFlagsLoading && !agentPresetsEnabled) {
      router.replace("/not-found")
    }
  }, [agentPresetsEnabled, featureFlagsLoading, router])

  if (featureFlagsLoading || !agentPresetsEnabled) {
    return <CenteredSpinner />
  }

  return (
    <div className="h-full">
      <AgentPresetsBuilder />
    </div>
  )
}
