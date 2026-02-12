"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { ScopeGuard } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { WorkspaceRbacRoles } from "@/components/workspaces/workspace-rbac-roles"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function WorkspaceRolesPage() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const rbacEnabled = isFeatureEnabled("rbac")

  useEffect(() => {
    if (!featureFlagsLoading && !rbacEnabled) {
      router.replace(`/workspaces/${workspaceId}/members`)
    }
  }, [featureFlagsLoading, rbacEnabled, router, workspaceId])

  if (featureFlagsLoading || !rbacEnabled) {
    return <CenteredSpinner />
  }
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
    <ScopeGuard scope="workspace:rbac:read" fallback={null} loading={null}>
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1200px] flex-col space-y-8 py-6">
          <div className="flex w-full">
            <div className="items-start space-y-3 text-left">
              <h2 className="text-2xl font-semibold tracking-tight">Roles</h2>
              <p className="text-md text-muted-foreground">
                Manage workspace roles and permissions.
              </p>
            </div>
          </div>
          <WorkspaceRbacRoles workspaceId={workspace.id} hideCreateButton />
        </div>
      </div>
    </ScopeGuard>
  )
}
