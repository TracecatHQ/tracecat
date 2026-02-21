"use client"

import { useParams } from "next/navigation"
import { useEffect } from "react"
import { AgentPresetsBuilder } from "@/components/agents/agent-presets-builder"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function AgentPresetsPage() {
  const params = useParams<{
    presetId: string
  }>()
  const presetId = params?.presetId
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  useEffect(() => {
    document.title = "Agent Presets"
  }, [])

  if (entitlementsLoading) {
    return <CenteredSpinner />
  }

  if (!agentAddonsEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="mx-auto flex w-full h-full max-w-3xl flex-1 items-center justify-center py-12">
          <EntitlementRequiredEmptyState
            title="Enterprise only"
            description="Advanced AI agents (human-in-the-loop and subagents) are only available on enterprise plans."
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
