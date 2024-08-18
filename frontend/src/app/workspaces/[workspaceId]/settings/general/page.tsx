"use client"

import { WorkspaceResponse } from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"

import { useWorkspaceManager } from "@/lib/hooks"
import { Separator } from "@/components/ui/separator"
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
    <div className="container-sm space-y-6">
      <div className="flex items-end justify-between">
        <h3 className="text-lg font-semibold">Workspace</h3>
      </div>
      <Separator />
      <div className="space-y-8">
        <div className="space-y-2 text-sm">
          <h6 className="text-sm font-semibold">General</h6>
          <WorkspaceGeneralSettings workspace={workspace} />
        </div>
        {isAdmin && (
          <div className="space-y-2 text-sm">
            <h6 className="text-sm font-semibold text-rose-500">Danger Zone</h6>
            <DangerZone workspace={workspace} />
          </div>
        )}
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
    <div className="space-y-4">
      <ConfirmDelete workspaceName={workspace.name} onDelete={handleDelete} />
    </div>
  )
}
