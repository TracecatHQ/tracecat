"use client"

import { useEffect } from "react"
import { FeatureFlagEmptyState } from "@/components/feature-flag-empty-state"
import { InboxLayout } from "@/components/inbox"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useInbox } from "@/hooks/use-inbox"

export default function InboxPage() {
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentApprovalsEnabled = isFeatureEnabled("agent-approvals")
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")
  const agentsFeatureEnabled = agentApprovalsEnabled && agentPresetsEnabled

  const {
    items: inboxItems,
    selectedId,
    setSelectedId,
    isLoading: inboxIsLoading,
    error: inboxError,
  } = useInbox({ enabled: agentsFeatureEnabled })

  useEffect(() => {
    document.title = "Inbox"
  }, [])

  if (featureFlagsLoading) {
    return <CenteredSpinner />
  }

  if (!agentsFeatureEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="mx-auto flex h-full w-full max-w-3xl flex-1 items-center justify-center py-12">
          <FeatureFlagEmptyState
            title="Enterprise only"
            description="Advanced AI agents (human-in-the-loop and subagents) are only available on enterprise plans."
          />
        </div>
      </div>
    )
  }

  return (
    <div className="size-full overflow-hidden">
      <InboxLayout
        items={inboxItems}
        selectedId={selectedId}
        onSelect={setSelectedId}
        isLoading={inboxIsLoading}
        error={inboxError ?? null}
      />
    </div>
  )
}
