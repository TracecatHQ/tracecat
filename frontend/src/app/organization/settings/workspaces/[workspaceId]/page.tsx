"use client"

import { useQuery } from "@tanstack/react-query"
import { useParams, useRouter } from "next/navigation"
import { workspacesGetWorkspace } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { OrgWorkspaceSettings } from "@/components/organization/org-workspace-settings"

export default function OrganizationWorkspaceSettingsPage() {
  const params = useParams<{ workspaceId: string }>()
  const router = useRouter()
  const canAdministerOrg = useScopeCheck("org:update")
  const canUpdateWorkspace = useScopeCheck("workspace:update")

  const workspaceId = params?.workspaceId

  const {
    data: workspace,
    isLoading: workspaceLoading,
    error: workspaceError,
  } = useQuery({
    queryKey: ["workspace", workspaceId],
    queryFn: async () => {
      if (!workspaceId) throw new Error("Invalid workspace ID")
      return await workspacesGetWorkspace({ workspaceId })
    },
    enabled: !!workspaceId,
  })

  if (!params || !workspaceId) {
    return <AlertNotification level="error" message="Invalid workspace ID." />
  }

  if (workspaceLoading) {
    return <CenteredSpinner />
  }

  if (workspaceError || !workspace) {
    return (
      <AlertNotification level="error" message="Error loading workspace." />
    )
  }

  if (!canAdministerOrg && !canUpdateWorkspace) {
    return (
      <AlertNotification
        level="error"
        message="You don't have permission to manage this workspace."
      />
    )
  }

  const handleWorkspaceDeleted = () => {
    router.push("/organization/settings/workspaces")
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Workspace settings
            </h2>
            <p className="text-md text-muted-foreground">
              Manage settings for{" "}
              <b className="inline-block">{workspace.name}</b>.
            </p>
          </div>
        </div>
        <OrgWorkspaceSettings
          workspace={workspace}
          onWorkspaceDeleted={handleWorkspaceDeleted}
        />
      </div>
    </div>
  )
}
