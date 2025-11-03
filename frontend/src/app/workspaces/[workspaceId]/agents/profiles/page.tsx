"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { AgentProfilesBuilder } from "@/components/agents/agent-profiles-builder"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"

export default function AgentProfilesPage() {
  const router = useRouter()
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentProfilesEnabled = isFeatureEnabled("agent-approvals")

  useEffect(() => {
    document.title = "Agent Profiles"
  }, [])

  useEffect(() => {
    if (!featureFlagsLoading && !agentProfilesEnabled) {
      router.replace("/not-found")
    }
  }, [agentProfilesEnabled, featureFlagsLoading, router])

  if (featureFlagsLoading || !agentProfilesEnabled) {
    return <CenteredSpinner />
  }

  return (
    <div className="h-full">
      <AgentProfilesBuilder />
    </div>
  )
}
