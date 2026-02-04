"use client"

import { useEffect } from "react"
import { AgentsTable } from "@/components/agents/agents-table"
import { FeatureFlagEmptyState } from "@/components/feature-flag-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function AgentsPage() {
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const agentApprovalsEnabled = hasEntitlement("agent_approvals")
  const agentPresetsEnabled = hasEntitlement("agent_presets")
  const agentsFeatureEnabled = agentApprovalsEnabled && agentPresetsEnabled

  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title = "Agents"
    }
  }, [])

  if (entitlementsLoading) {
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
    <div className="size-full overflow-auto px-3 py-6 space-y-6">
      <AgentsTable />
    </div>
  )
}
