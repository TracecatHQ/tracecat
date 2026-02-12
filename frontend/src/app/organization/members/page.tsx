"use client"

import { ScopeGuard } from "@/components/auth/scope-guard"
import { OrgMembersTable } from "@/components/organization/org-members-table"
import { OrgRbacGroups } from "@/components/organization/org-rbac-groups"
import { OrgRbacRoles } from "@/components/organization/org-rbac-roles"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useFeatureFlag } from "@/hooks/use-feature-flags"

const tabTriggerClassName =
  "rounded-none border-b-2 border-transparent px-4 py-2.5 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"

export default function MembersPage() {
  const { isFeatureEnabled } = useFeatureFlag()
  const rbacEnabled = isFeatureEnabled("rbac")

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1200px] flex-col space-y-8 py-6">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Members and access control
            </h2>
            <p className="text-md text-muted-foreground">
              Manage organization members, roles, and groups.
            </p>
          </div>
        </div>
        <Tabs defaultValue="members" className="w-full">
          <TabsList className="inline-flex h-auto w-auto justify-start gap-0 rounded-none border-b border-border/30 bg-transparent p-0">
            <TabsTrigger value="members" className={tabTriggerClassName}>
              Members
            </TabsTrigger>
            {rbacEnabled && (
              <ScopeGuard scope="org:rbac:read" fallback={null} loading={null}>
                <TabsTrigger value="roles" className={tabTriggerClassName}>
                  Roles
                </TabsTrigger>
                <TabsTrigger value="groups" className={tabTriggerClassName}>
                  Groups
                </TabsTrigger>
              </ScopeGuard>
            )}
          </TabsList>
          <TabsContent value="members" className="mt-6">
            <OrgMembersTable />
          </TabsContent>
          {rbacEnabled && (
            <ScopeGuard scope="org:rbac:read" fallback={null} loading={null}>
              <TabsContent value="roles" className="mt-6">
                <OrgRbacRoles />
              </TabsContent>
              <TabsContent value="groups" className="mt-6">
                <OrgRbacGroups />
              </TabsContent>
            </ScopeGuard>
          )}
        </Tabs>
      </div>
    </div>
  )
}
