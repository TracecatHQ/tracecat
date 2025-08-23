"use client"

import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { WorkspaceSecretsTable } from "@/components/workspaces/workspace-secrets-table"
import { useWorkspaceDetails } from "@/hooks/use-workspace"

export default function WorkspaceCredentialsPage() {
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  if (workspaceLoading) {
    return <CenteredSpinner />
  }
  if (workspaceError) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full flex-col space-y-12 py-8">
          <AlertNotification
            level="error"
            message="Error loading workspace info."
          />
        </div>
      </div>
    )
  }
  if (!workspace) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full flex-col space-y-12 py-8">
          <AlertNotification level="error" message="Workspace not found." />
        </div>
      </div>
    )
  }
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full flex-col space-y-12 py-8">
        <WorkspaceSecretsTable />
      </div>
    </div>
  )
}
