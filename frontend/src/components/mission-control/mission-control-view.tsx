"use client"

import { useScopeCheck } from "@/components/auth/scope-guard"
import { ChatInterface } from "@/components/chat/chat-interface"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useWorkspaceId } from "@/providers/workspace-id"

export function MissionControlView() {
  const workspaceId = useWorkspaceId()
  const canExecuteAgents = useScopeCheck("agent:execute")
  const { hasEntitlement, isLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  if (isLoading || canExecuteAgents === undefined) {
    return <CenteredSpinner />
  }

  if (!canExecuteAgents) {
    return null
  }

  if (!agentAddonsEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="mx-auto flex h-full w-full max-w-3xl flex-1 items-center justify-center py-12">
          <EntitlementRequiredEmptyState
            title="Upgrade required"
            description="Mission Control is unavailable on your current plan."
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex size-full min-h-0 flex-col overflow-hidden">
      <ChatInterface
        entityType="copilot"
        entityId={workspaceId}
        title="Mission Control"
        bodyClassName="min-h-0"
        placeholder="Ask Mission Control..."
      />
    </div>
  )
}
