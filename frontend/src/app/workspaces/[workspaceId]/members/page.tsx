"use client"

import { ScopeGuard } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { WorkspaceMembersTable } from "@/components/workspaces/workspace-members-table"
import { WorkspaceRbacGroups } from "@/components/workspaces/workspace-rbac-groups"
import { WorkspaceRbacRoles } from "@/components/workspaces/workspace-rbac-roles"
import { useWorkspaceDetails } from "@/hooks/use-workspace"

const tabTriggerClassName =
  "rounded-none border-b-2 border-transparent px-4 py-2.5 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"

export default function WorkspaceMembersPage() {
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
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
      <div className="container flex h-full max-w-[1200px] flex-col space-y-8 py-6">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Members</h2>
            <p className="text-md text-muted-foreground">
              Manage workspace members, roles, and groups.
            </p>
          </div>
        </div>
        <Tabs defaultValue="members" className="w-full">
          <TabsList className="inline-flex h-auto w-auto justify-start gap-0 rounded-none border-b border-border/30 bg-transparent p-0">
            <TabsTrigger value="members" className={tabTriggerClassName}>
              Members
            </TabsTrigger>
            <ScopeGuard
              scope="workspace:rbac:read"
              fallback={null}
              loading={null}
            >
              <TabsTrigger value="roles" className={tabTriggerClassName}>
                Roles
              </TabsTrigger>
              <TabsTrigger value="groups" className={tabTriggerClassName}>
                Groups
              </TabsTrigger>
            </ScopeGuard>
          </TabsList>
          <TabsContent value="members" className="mt-6">
            <WorkspaceMembersTable workspace={workspace} />
          </TabsContent>
          <ScopeGuard
            scope="workspace:rbac:read"
            fallback={null}
            loading={null}
          >
            <TabsContent value="roles" className="mt-6">
              <WorkspaceRbacRoles workspaceId={workspace.id} />
            </TabsContent>
            <TabsContent value="groups" className="mt-6">
              <WorkspaceRbacGroups workspaceId={workspace.id} />
            </TabsContent>
          </ScopeGuard>
        </Tabs>
      </div>
    </div>
  )
}
