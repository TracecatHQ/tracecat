"use client"

import { OrgSettingsSecurityForm } from "@/components/organization/org-settings-security"

export default function SecuritySettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              IP allowlist
            </h2>
            <p className="text-base text-muted-foreground">
              Restrict access to this organization to trusted IP addresses, such
              as your VPN egress ranges.
            </p>
          </div>
        </div>

        <OrgSettingsSecurityForm />
      </div>
    </div>
  )
}
