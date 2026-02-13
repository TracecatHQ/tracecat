"use client"

import { OrgMembersTable } from "@/components/organization/org-members-table"

export default function MembersPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Organization members
            </h2>
            <p className="text-base text-muted-foreground">
              Manage members and invitations for your organization.
            </p>
          </div>
        </div>
        <OrgMembersTable />
      </div>
    </div>
  )
}
