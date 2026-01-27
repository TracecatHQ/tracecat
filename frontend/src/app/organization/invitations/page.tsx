"use client"

import { OrgInvitationsTable } from "@/components/organization/org-invitations-table"

export default function InvitationsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Organization invitations
            </h2>
            <p className="text-md text-muted-foreground">
              Manage invitations to join your organization.
            </p>
          </div>
        </div>
        <div className="space-y-4">
          <h6 className="text-sm font-semibold">Pending invitations</h6>
          <OrgInvitationsTable />
        </div>
      </div>
    </div>
  )
}
