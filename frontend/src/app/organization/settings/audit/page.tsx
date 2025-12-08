"use client"

import { OrgSettingsAuditForm } from "@/components/organization/org-settings-audit"

export default function AuditSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Audit logging
            </h2>
            <p className="text-md text-muted-foreground">
              Configure audit log delivery to your webhook endpoint.
            </p>
          </div>
        </div>

        <OrgSettingsAuditForm />
      </div>
    </div>
  )
}
