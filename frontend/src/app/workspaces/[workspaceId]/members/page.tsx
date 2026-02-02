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
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Members</h2>
            <p className="text-md text-muted-foreground">
              Manage workspace members, roles, and groups.
            </p>
          </div>
        </div>
        <WorkspaceMembersTable workspace={workspace} />
      </div>
    </div>
  )
}
