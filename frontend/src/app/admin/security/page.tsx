"use client"

import { PlatformSecuritySettings } from "@/components/admin/platform-security-settings"
import { MfaPolicyCard } from "@/components/settings/mfa-policy-card"
import { SecuritySettings } from "@/components/settings/security-settings"

export default function AdminSecuritySettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="space-y-4">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Multi-factor authentication
            </h2>
            <p className="text-base text-muted-foreground">
              Once you register a security key, privileged platform admin
              actions, such as deleting organizations or inviting users, ask you
              to verify with it if you haven&apos;t in the last 5 minutes.
            </p>
          </div>
          <MfaPolicyCard
            scope="platform"
            description="Require platform admins to verify with a security key or passkey at every sign-in. Admins without a key must register one at their next sign-in."
          />
          <div className="rounded-md border p-4">
            <SecuritySettings />
          </div>
        </div>

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
