"use client"

import { useWorkspace } from "@/lib/hooks"
import { Separator } from "@/components/ui/separator"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { WprkspaceGeneralSettings } from "@/components/workspaces/workspace-general"

export default function WorkspaceGeneralSettingsPage() {
  const { workspace, workspaceError, workspaceLoading } = useWorkspace()
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
        <h3 className="text-lg font-medium">Workspace</h3>
      </div>
      <Separator />
      <WprkspaceGeneralSettings workspace={workspace} />
    </div>
  )
}
