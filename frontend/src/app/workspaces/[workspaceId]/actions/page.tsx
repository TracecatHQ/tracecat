"use client"

import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { WorkspaceActionsInventory } from "@/components/registry/workspace-actions-inventory"

export default function WorkspaceActionsPage() {
  const canReadRegistry = useScopeCheck("org:registry:read")

  if (canReadRegistry === undefined) {
    return <CenteredSpinner />
  }

  if (!canReadRegistry) {
    return null
  }

  return (
    <div className="size-full overflow-auto">
      <div className="flex h-full flex-col">
        <WorkspaceActionsInventory />
      </div>
    </div>
  )
}
