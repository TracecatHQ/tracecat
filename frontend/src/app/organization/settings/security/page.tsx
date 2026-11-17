"use client"

import { OrgSettingsSecurityForm } from "@/components/organization/org-settings-security"
import { MfaPolicyCard } from "@/components/settings/mfa-policy-card"

export default function SecuritySettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="space-y-4">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Multi-factor authentication
            </h2>
            <p className="text-base text-muted-foreground">
              Require members to verify with a hardware security key or passkey
              at every sign-in.
            </p>
          </div>
          <MfaPolicyCard
            scope="organization"
            description="Applies to password, Google/OIDC, and SAML sign-in. Members without a key must register one at their next sign-in. You need a key registered in Settings > Security first."
          />
        </div>

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
