"use client"

import { useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import { inboxListItems } from "@/client"
import { FeatureFlagEmptyState } from "@/components/feature-flag-empty-state"
import { InboxLayout } from "@/components/inbox"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { sortInboxItems } from "@/lib/inbox"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function InboxPage() {
  const workspaceId = useWorkspaceId()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const agentApprovalsEnabled = isFeatureEnabled("agent-approvals")
  const agentPresetsEnabled = isFeatureEnabled("agent-presets")
  const agentsFeatureEnabled = agentApprovalsEnabled && agentPresetsEnabled

  // Fetch inbox items from unified endpoint
  const {
    data: inboxData,
    isLoading: inboxIsLoading,
    error: inboxError,
  } = useQuery({
    queryKey: ["inbox", "list", workspaceId],
    queryFn: () => inboxListItems({ workspaceId }),
    refetchInterval: (query) => {
      // Poll faster if there are pending items
      const hasPending = query.state.data?.some(
        (item) => item.status === "pending"
      )
      return hasPending ? 3000 : 10000
    },
    enabled: agentsFeatureEnabled,
  })

  // Sort inbox items (backend already returns sorted, but ensure client-side consistency)
  const inboxItems = useMemo(() => {
    if (!inboxData) return []
    return sortInboxItems(inboxData)
  }, [inboxData])

  // Auto-select first pending item or first item
  useEffect(() => {
    if (inboxItems.length > 0 && !selectedId) {
      const pendingItem = inboxItems.find((item) => item.status === "pending")
      setSelectedId(pendingItem?.id ?? inboxItems[0].id)
    }
  }, [inboxItems, selectedId])

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
