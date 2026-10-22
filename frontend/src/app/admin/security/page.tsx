"use client"

import { PlatformSecuritySettings } from "@/components/admin/platform-security-settings"

export default function AdminSecuritySettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              IP allowlist
            </h2>
            <p className="text-base text-muted-foreground">
              Restrict platform admin access to trusted IP addresses, such as
              your VPN egress ranges.
            </p>
          </div>
        </div>

        <PlatformSecuritySettings />
      </div>
    </div>
  )
}
