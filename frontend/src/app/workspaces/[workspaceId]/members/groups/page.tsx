"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { ScopeGuard } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { WorkspaceRbacGroups } from "@/components/workspaces/workspace-rbac-groups"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function WorkspaceGroupsPage() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const rbacEnabled = hasEntitlement("rbac")

  useEffect(() => {
    if (!entitlementsLoading && !rbacEnabled) {
      router.replace(`/workspaces/${workspaceId}/members`)
    }
  }, [entitlementsLoading, rbacEnabled, router, workspaceId])

  if (entitlementsLoading || !rbacEnabled) {
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
    <ScopeGuard scope="org:rbac:read" fallback={null} loading={null}>
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1200px] flex-col space-y-8 py-6">
          <div className="flex w-full">
            <div className="items-start space-y-3 text-left">
              <h2 className="text-2xl font-semibold tracking-tight">Groups</h2>
              <p className="text-md text-muted-foreground">
                Manage workspace groups and their members.
              </p>
            </div>
          </div>
          <WorkspaceRbacGroups workspaceId={workspace.id} hideCreateButton />
        </div>
      </div>
    </ScopeGuard>
  )
}
