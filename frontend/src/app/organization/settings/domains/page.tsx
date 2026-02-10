"use client"

import { OrgSettingsDomains } from "@/components/organization/org-settings-domains"

export default function DomainsSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Domains</h2>
            <p className="text-base text-muted-foreground">
              View domains currently assigned to your organization.
            </p>
          </div>
        </div>
        <OrgSettingsDomains />
      </div>
    </div>
  )
}
