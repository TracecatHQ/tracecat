"use client"

import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { TablesDashboard } from "@/components/tables/tables-dashboard"
import { useWorkspaceDetails } from "@/hooks/use-workspace"

export default function TablesPage() {
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
      <div className="flex h-full flex-col">
        <TablesDashboard />
      </div>
    </div>
  )
}
