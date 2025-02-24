"use client"

import { WorkspaceResponse } from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"

import { useWorkspaceManager } from "@/lib/hooks"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { ConfirmDelete } from "@/components/workspaces/delete-workspace"
import { WorkspaceGeneralSettings } from "@/components/workspaces/workspace-general"

export default function WorkspaceGeneralSettingsPage() {
  const { workspace, workspaceError, workspaceLoading } = useWorkspace()
  const { user } = useAuth()
  const isAdmin = user?.is_superuser || user?.role === "admin"

  if (workspaceLoading) {
    return <CenteredSpinner />
  }
  if (!workspace || workspaceError) {
    return (
      <AlertNotification
        level="error"
        message="Error loading workspace info."
      />
    )
  }
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Workspace</h2>
            <p className="text-md text-muted-foreground">
              Manage general settings for the workspace.
            </p>
          </div>
        </div>

        <div className="space-y-14">
          <div className="flex items-center gap-4">
            <WorkspaceGeneralSettings workspace={workspace} />
          </div>
          {isAdmin && (
            <div className="space-y-4">
              <div className="space-y-2">
                <h6 className="text-md text-rose-500">Danger zone</h6>
                <p className="text-sm text-muted-foreground">
                  Once you delete a workspace, there is no going back. Please be
                  certain.
                </p>
              </div>
              <DangerZone workspace={workspace} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function DangerZone({ workspace }: { workspace: WorkspaceResponse }) {
  const { deleteWorkspace } = useWorkspaceManager()
  const handleDelete = async () => {
    console.log("Delete workspace", workspace)
    try {
      await deleteWorkspace(workspace.id)
    } catch (error) {
      console.error("Error deleting workspace", error)
    }
  }

  return (
    <ConfirmDelete workspaceName={workspace.name} onDelete={handleDelete} />
  )
}
