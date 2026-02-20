"use client"

import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { OrgRbacAssignments } from "@/components/organization/org-rbac-assignments"
import { OrgRbacGroups } from "@/components/organization/org-rbac-groups"
import { OrgRbacRoles } from "@/components/organization/org-rbac-roles"
import { OrgRbacScopes } from "@/components/organization/org-rbac-scopes"
import { OrgRbacUserAssignments } from "@/components/organization/org-rbac-user-assignments"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useFeatureFlag } from "@/hooks/use-feature-flags"

type RbacTab =
  | "roles"
  | "groups"
  | "scopes"
  | "assignments"
  | "user-assignments"

export default function RbacSettingsPage() {
  const router = useRouter()
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const rbacEnabled = isFeatureEnabled("rbac")
  const [activeTab, setActiveTab] = useState<RbacTab>("roles")

  useEffect(() => {
    if (!featureFlagsLoading && !rbacEnabled) {
      router.replace("/organization/members")
    }
  }, [featureFlagsLoading, rbacEnabled, router])

  if (featureFlagsLoading || !rbacEnabled) {
    return <CenteredSpinner />
  }

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
          <TabsList className="inline-flex h-auto w-auto justify-start gap-0 rounded-none border-b border-border/30 bg-transparent p-0">
            <TabsTrigger
              value="roles"
              className="rounded-none border-b-2 border-transparent px-4 py-2.5 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              Roles
            </TabsTrigger>
            <TabsTrigger
              value="groups"
              className="rounded-none border-b-2 border-transparent px-4 py-2.5 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              Groups
            </TabsTrigger>
            <TabsTrigger
              value="assignments"
              className="rounded-none border-b-2 border-transparent px-4 py-2.5 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              Group assignments
            </TabsTrigger>
            <TabsTrigger
              value="user-assignments"
              className="rounded-none border-b-2 border-transparent px-4 py-2.5 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              User assignments
            </TabsTrigger>
            <TabsTrigger
              value="scopes"
              className="rounded-none border-b-2 border-transparent px-4 py-2.5 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              Scopes
            </TabsTrigger>
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
