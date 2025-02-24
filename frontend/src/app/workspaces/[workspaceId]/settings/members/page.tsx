"use client"

import { useWorkspace } from "@/providers/workspace"

import { Separator } from "@/components/ui/separator"
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
    <div className="h-full space-y-6">
      <div className="flex items-end justify-between">
        <h3 className="text-lg font-semibold">Members</h3>
      </div>
      <p className="text-sm text-muted-foreground">
        Manage who is a member of{" "}
        <b className="inline-block">{workspace.name}</b> workspace
      </p>
      <Separator className="my-6" />
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
  )
}
