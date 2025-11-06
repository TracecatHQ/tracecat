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
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")

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
      <AgentPresetsBuilder presetId={presetId} />
    </div>
  )
}
