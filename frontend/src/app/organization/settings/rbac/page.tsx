"use client"

import { useState } from "react"
import { OrgRbacAssignments } from "@/components/organization/org-rbac-assignments"
import { OrgRbacGroups } from "@/components/organization/org-rbac-groups"
import { OrgRbacRoles } from "@/components/organization/org-rbac-roles"
import { OrgRbacScopes } from "@/components/organization/org-rbac-scopes"
import { OrgRbacUserAssignments } from "@/components/organization/org-rbac-user-assignments"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

type RbacTab =
  | "roles"
  | "groups"
  | "scopes"
  | "assignments"
  | "user-assignments"

export default function RbacSettingsPage() {
  const [activeTab, setActiveTab] = useState<RbacTab>("roles")

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1200px] flex-col space-y-8 py-6">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Access control
            </h2>
            <p className="text-md text-muted-foreground">
              Manage roles, groups, and permissions for your organization.
              Configure fine-grained access control with scopes and assign
              permissions to users and groups.
            </p>
          </div>
        </div>

        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as RbacTab)}
          className="w-full"
        >
          <TabsList className="grid w-full max-w-[650px] grid-cols-5">
            <TabsTrigger value="roles">Roles</TabsTrigger>
            <TabsTrigger value="groups">Groups</TabsTrigger>
            <TabsTrigger value="assignments">Group assignments</TabsTrigger>
            <TabsTrigger value="user-assignments">User assignments</TabsTrigger>
            <TabsTrigger value="scopes">Scopes</TabsTrigger>
          </TabsList>

          <TabsContent value="roles" className="mt-6">
            <OrgRbacRoles />
          </TabsContent>

          <TabsContent value="groups" className="mt-6">
            <OrgRbacGroups />
          </TabsContent>

          <TabsContent value="assignments" className="mt-6">
            <OrgRbacAssignments />
          </TabsContent>

          <TabsContent value="user-assignments" className="mt-6">
            <OrgRbacUserAssignments />
          </TabsContent>

          <TabsContent value="scopes" className="mt-6">
            <OrgRbacScopes />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
