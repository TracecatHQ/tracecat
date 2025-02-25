"use client"

import { useWorkspace } from "@/providers/workspace"

import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { AddWorkspaceMember } from "@/components/workspaces/add-workspace-member"
import { WorkspaceMembersTable } from "@/components/workspaces/workspace-members-table"

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
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Members</h2>
            <p className="text-md text-muted-foreground">
              Manage who is a member of{" "}
              <b className="inline-block">{workspace.name}</b> workspace.
            </p>
          </div>
        </div>

        <div className="space-y-4">
          <>
            <h6 className="text-sm font-semibold">Invite members</h6>
            <AddWorkspaceMember workspace={workspace} />
          </>
          <>
            <h6 className="text-sm font-semibold">Manage members</h6>
            <WorkspaceMembersTable workspace={workspace} />
          </>
        </div>
      </div>
    </div>
  )
}
