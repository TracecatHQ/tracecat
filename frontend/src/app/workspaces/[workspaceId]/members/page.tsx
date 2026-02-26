"use client"

import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { WorkspaceMembersTable } from "@/components/workspaces/workspace-members-table"
import { useWorkspaceDetails } from "@/hooks/use-workspace"

export default function WorkspaceMembersPage() {
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  if (workspaceLoading) {
    return <CenteredSpinner />
  }
  if (workspaceError) {
    return (
      <AlertNotification
        level="error"
        message="Error loading workspace info."
      />
    )
  }
  if (!workspace) {
    return <AlertNotification level="error" message="Workspace not found." />
  }
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1200px] flex-col space-y-8 py-6">
        <WorkspaceMembersTable workspace={workspace} />
      </div>
    </div>
  )
}
