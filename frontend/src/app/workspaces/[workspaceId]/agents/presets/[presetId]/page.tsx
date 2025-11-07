"use client"

import { useParams, useRouter } from "next/navigation"
import { useEffect } from "react"
import { AgentPresetsBuilder } from "@/components/agents/agent-presets-builder"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"

export default function AgentPresetsPage() {
  const params = useParams<{
    presetId: string
  }>()
  const router = useRouter()
  const presetId = params?.presetId
  const {
    isFeatureEnabled,
    isLoading: featureFlagsLoading,
    hasFeatureData,
  } = useFeatureFlag()
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")

  useEffect(() => {
    document.title = "Agent Presets"
  }, [])

  useEffect(() => {
    if (!featureFlagsLoading && hasFeatureData && !agentPresetsEnabled) {
      router.replace("/not-found")
    }
  }, [agentPresetsEnabled, featureFlagsLoading, hasFeatureData, router])

  if (featureFlagsLoading || !agentPresetsEnabled) {
    return <CenteredSpinner />
  }

  return (
    <div className="h-full">
      <AgentPresetsBuilder presetId={presetId} />
    </div>
  )
}
