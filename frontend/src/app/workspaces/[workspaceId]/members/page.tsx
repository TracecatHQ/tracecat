"use client"

import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { WorkspaceMembersTable } from "@/components/workspaces/workspace-members-table"
import { useWorkspace } from "@/providers/workspace"

export default function WorkspaceMembersPage() {
  const { workspace, workspaceError, workspaceLoading } = useWorkspace()
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
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        <div className="space-y-4">
          <WorkspaceMembersTable workspace={workspace} />
        </div>
      </div>
    </div>
  )
}
